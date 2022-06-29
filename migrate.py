#  © 2021 Amazon Web Services, Inc. or its affiliates. All Rights Reserved.
#  This AWS Content is provided subject to the terms of the AWS Customer Agreement available at
#  http://aws.amazon.com/agreement or other written agreement between Customer and either
#  Amazon Web Services, Inc. or Amazon Web Services EMEA SARL or both.
#  The sample code; software libraries; command line tools; proofs of concept; templates; or other
#  related technology (including any of the foregoing that are provided by our personnel)
#  is provided to you as AWS Content under the AWS Customer Agreement, or the relevant
#  written agreement between you and AWS (whichever applies). You should not use this
#  AWS Content in your production accounts, or on production or other critical data. You
#  are responsible for testing, securing, and optimizing the AWS Content, such as sample
#  code, as appropriate for production grade use based on your specific quality control
#  practices and standards. Deploying AWS Content may incur AWS charges for creating or
#  using AWS chargeable resources, such as running Amazon EC2 instances or using Amazon S3 storage.

# This script is intended to migrate stack instances
# from one stackset to another stackset (service_managed to self_managed)

# TODO : All or one OU at a time ? ALL stacks are migrated
# TODO : Check token expiration for boto3.Session()
# TODO : Migrate to step function ?


"""
    Key principles for service_managed stacksets:
    1. They are attached to OU
    2. They apply to accounts in OU and child OUs
        -> Child OU are not taken into account for this scenario
    3. They are identical between accounts in the same OU
        -> can't have discrepencies in regions
"""

import argparse
import itertools
import sys
import time

import boto3

import logging
import difflib

from botocore.exceptions import ClientError
from utils import assume_role, get_accounts_from_ou, get_all_accounts


ACCOUNTS = []
NO_CHANGES = "The submitted information didn't contain changes. Submit different information to create a change set."

session = boto3.Session()


class StackSet:
    """This class implements method for stackset manipulation"""

    # pylint: disable=too-many-instance-attributes
    # 9 is reasonable in this case.

    def __init__(self, name: str) -> None:
        self.instances = []
        self.filtered_instances = []
        self.name = name
        self.parameters = None
        self.parameters_override = []
        self.non_current_stacks = []
        self.drifted_stacks = []
        self.regions = []
        self.template = None
        self.ous = []
        self.target_accounts = []
        self.extra_stacks = []
        self.execution_role_name = ""
        self.capabilities = []

    def load(self, _accounts):
        """Load state of the stackset"""
        logger.info(f"Loading details for StackSet {self.name}")
        self.__get_stack_set()
        self.__fetch_stack_instances()
        self.__filter_instances(_accounts)

    def get_stack_instances(self):
        """Return the list of stack instances"""
        return self.instances

    def __get_stack_set(self):
        """Load the object with stackset details (template and parameters)"""
        client = session.client("cloudformation")
        response = client.describe_stack_set(StackSetName=self.name)
        self.parameters = response["StackSet"]["Parameters"]
        self.template = response["StackSet"]["TemplateBody"]
        self.ous = response["StackSet"].get("OrganizationalUnitIds", [])
        self.target_accounts = self.get_target_accounts()
        self.execution_role_name = response["StackSet"].get("ExecutionRoleName")
        self.capabilities = response["StackSet"].get("Capabilities",[])

    def __fetch_stack_instances(self):
        """Load the object with stack instances"""
        client = session.client("cloudformation")
        response = client.list_stack_instances(StackSetName=self.name)
        instances = []
        instances.extend(
            [
                i.get("StackId", f"arn:aws:cloudformation:{i['Region']}:{i['Account']}")
                for i in response["Summaries"]
            ]
        )
        while "NextToken" in response:
            response = client.list_stack_instances(
                StackSetName=self.name, NextToken=response["NextToken"]
            )
            instances.extend(
                [
                    i.get(
                        "StackId",
                        f"arn:aws:cloudformation:{i['Region']}:{i['Account']}:non-existant-stack",
                    )
                    for i in response["Summaries"]
                ]
            )

        self.instances = instances

    def __filter_instances(self, _accounts: list):
        """Create a filtered list of instances for only a subset of AWS accounts."""
        self.filtered_instances = list(
            filter(lambda x: x.split(":")[4] in _accounts, self.instances)
        )

    def evaluate_stack_sync(self):
        """Check the status of each stack instance (parameter overrides, status and drift)"""
        client = session.client("cloudformation")
        for instance in self.instances:
            logger.debug(instance)
            region = instance.split(":")[3]
            account = instance.split(":")[4]
            try:
                response = client.describe_stack_instance(
                    StackInstanceAccount=account,
                    StackInstanceRegion=region,
                    StackSetName=self.name,
                )
            except ClientError as e:
                if e.response["Error"]["Code"] == "Throttling":
                    time.sleep(5)
                    response = client.describe_stack_instance(
                        StackInstanceAccount=account,
                        StackInstanceRegion=region,
                        StackSetName=self.name,
                    )

            if "ParameterOverrides" in response["StackInstance"]:
                if len(response["StackInstance"]["ParameterOverrides"]) > 0:
                    self.parameters_override.append(instance)
            if response["StackInstance"]["Status"] != "CURRENT":
                self.non_current_stacks.append(instance)

            if response["StackInstance"]["DriftStatus"] in ["DRIFTED", "UNKNOWN"]:
                self.drifted_stacks.append(instance)

            if account not in self.target_accounts:
                self.extra_stacks.append(instance)

    def evaluate_regions(self):
        """
        Check if the stack instances are uniformly deployed across
        AWS Regions (ou-a -> region a, ou-b -> region a...)
        """
        instances_map = {}
        for instance in self.instances:
            region = instance.split(":")[3]
            if region in instances_map:
                instances_map[region] += 1
            else:
                instances_map[region] = 1
        self.regions = list(instances_map.keys())
        regions = itertools.groupby(instances_map.values())
        next(regions, None)
        if next(regions, None) is None:
            return False
        logger.info(
            "stack instances are not deployed to the same regions across all accounts"
        )
        return True

    def detect_drift(self):
        """Start the detection of the drift for the stackset and wait for its completion"""
        client = session.client("cloudformation")
        response = client.detect_stack_set_drift(
            StackSetName=self.name,
            OperationPreferences={
                "RegionConcurrencyType": "PARALLEL",
                "FailureTolerancePercentage": 100,
                "MaxConcurrentPercentage": 100,
            },
        )
        self.wait_operation_is_complete(response["OperationId"])

    def delete_stack_instances(self, organizational_units):
        """Delete stack instances from the stackset for one OU and retain the stack instances"""
        logger.info(
            f"Starting to delete {len(self.filtered_instances)} stack instances from {self.name}"
        )
        with open(f"{self.name}-instances-deleted.txt", "w") as f:
            f.write("\n".join(self.filtered_instances))
        client = session.client("cloudformation")
        response = client.delete_stack_instances(
            StackSetName=self.name,
            RetainStacks=True,
            DeploymentTargets={"OrganizationalUnitIds": organizational_units},
            Regions=self.regions,
            OperationPreferences={
                "RegionConcurrencyType": "PARALLEL",
                "MaxConcurrentCount": 10, # Improve to 100% as we do retain
            },
        )
        self.wait_operation_is_complete(response["OperationId"])

    def wait_operation_is_complete(self, operation_id):
        """Simple waiter for cloudformation stackset operation"""

        client = session.client("cloudformation")
        response = client.describe_stack_set_operation(
            StackSetName=self.name, OperationId=operation_id
        )
        while not response["StackSetOperation"]["Status"] in ["FAILED", "SUCCEEDED"]:
            time.sleep(5)
            response = client.describe_stack_set_operation(
                StackSetName=self.name, OperationId=operation_id
            )
            logger.info(
                f"The {response['StackSetOperation']['Action']} Operation id {operation_id} \
    has status {response['StackSetOperation']['Status']}"
            )

    def import_stack(self, instances):
        """Impport stack instances into a stackset."""
        logger.info(f"Starting to migrate {len(instances)} instances into {self.name}")
        client = boto3.client("cloudformation")
        for i in range(0, len(instances), 10):
            logger.info(f"Import stack instances from {i} to {i+10}")
            response = client.import_stacks_to_stack_set(
                StackSetName=self.name,
                StackIds=instances[i : i + 10],
                OperationPreferences={
                    "RegionConcurrencyType": "PARALLEL",
                    "FailureToleranceCount": 10,
                },
            )
            self.wait_operation_is_complete(response["OperationId"])        

    def generate_reports(self):
        with open(f"reports/report_stackset_{self.name}-drift.txt", "w") as f:
            f.write("\n".join(self.drifted_stacks))
        with open(f"reports/report_stackset_{self.name}-parameter.txt", "w") as f:
            f.write("\n".join(self.parameters_override))
        with open(f"reports/report_stackset_{self.name}-noncurrent.txt", "w") as f:
            f.write("\n".join(self.non_current_stacks))
        with open(f"reports/report_stackset_{self.name}-extras.txt", "w") as f:
            f.write("\n".join(self.extra_stacks))

    def get_target_accounts(self):
        _accounts = []
        for ou in self.ous:
            logger.info(f"Get all accounts for OU {ou}")
            if ou.startswith("ou-"):
                _accounts.extend(get_accounts_from_ou(session, ou))
            elif ou.startswith("r-"):
                _accounts.extend(get_all_accounts(session))
        final_list = list(set(_accounts))
        logger.info(
            f"Evaluated targets to {len(final_list)} accounts for this Stackset"
        )
        return final_list


def setup_args():
    """This function parses the CLI arguments"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-s", "--source-stack-set-name", help="Source stack set name", required=True
    )
    parser.add_argument(
        "-t", "--target-stack-set-name", help="Target stack set name", required=False
    )
    parser.add_argument(
        "-o", "--organizational-unit", help="Organizational Unit to migrate"
    )
    parser.add_argument(
        "-d",
        "--disable-drift",
        help="Disable drift detection. However script still \
            checks for drift to be IN-SYNC",
        action="store_true",
    )
    parser.add_argument("-c", "--change-set", help="Enable change set evaluation for each stack instance", action="store_true")
    parsed_args = parser.parse_args()
    if parsed_args.change_set and not parsed_args.target_stack_set_name:
        print("Can't check change set without a target stack set. Please add --target-stack-set-name")
        sys.exit(1)
    if parsed_args.source_stack_set_name == parsed_args.target_stack_set_name:
        print("Cant migrate to the same AWS CloudFormation StackSet")
        sys.exit(1)
    
    return parsed_args
  

def evaluate_change_set(stack_instance:str, target_stack_set:StackSet):
    logger.info(f"Evaluating a change set for {stack_instance}")
    account_id = stack_instance.split(':')[4]
    region = stack_instance.split(':')[3]
    _session = assume_role(account_id,target_stack_set.execution_role_name,region)
    _client = _session.client('cloudformation')
    response = _client.create_change_set(
        StackName=stack_instance,
        TemplateBody=target_stack_set.template,
        ChangeSetName='stackset-migration',
        ChangeSetType='UPDATE',
        Capabilities=target_stack_set.capabilities
    )
    changeset_id = response['Id']
    response = _client.describe_change_set(
        ChangeSetName=changeset_id
    )
    waiter = 10
    while response['ExecutionStatus'] != 'AVAILABLE' and response['Status'] != 'FAILED' and waiter > 0:
        waiter += -1
        time.sleep(5)
        response = _client.describe_change_set(
            ChangeSetName=changeset_id
        )
        logger.info(response['ExecutionStatus'])
        logger.info(response['Status'])
    _client.delete_change_set(
            ChangeSetName = changeset_id
        )
    
    if response['Status'] == 'FAILED' and response['StatusReason'] == NO_CHANGES:
        return 0
    else:
        return len(response['Changes'])


def instance_already_exist(instance, instances):
    for i in instances:
        if instance.split(':')[3:5] == i.split(':')[3:5]:
            return True
    return False


def compare_stack_sets(source_stack_set:StackSet, target_stack_set:StackSet=None, detect_change_set=False):
    exit_code = 0
    
    # check if there are some drifted stacks
    if source_stack_set.drifted_stacks:
        logger.error("This stackset has drifted stacks. This is not supported. Please fix the accounts and regions first.")
        for instance in source_stackset.drifted_stacks:
            logger.info(instance)
        exit_code=1

    # check if some stacks have parameter overrides
    if source_stack_set.parameters_override:
        logger.error("This stackset uses parameter overrides. This is not supported. Please fix the accounts and regions first.")
        for instance in source_stackset.parameters_override:
            logger.info(instance)
        exit_code=1
    
    # check if some stacks are in "non current" state (INOPERABLE/OUTDATED)
    if source_stack_set.non_current_stacks:
        logger.error("This stackset has non current stacks. This is not supported. Please fix the accounts and regions first.")
        for instance in source_stackset.non_current_stacks:
            logger.info(instance)
        exit_code=1
    
    # Check if stackset is deployed uniformly on AWS regions
    if source_stack_set.evaluate_regions():
        logger.error("This stackset is not deployed to the same regions for all accounts. Please fix the account and regions first.")
        exit_code=1
        
    if target_stack_set:
        # check if templates are the same in source and target. Could be improved by relying on ChangeSet only instead of string comparison
        if source_stack_set.template != target_stack_set.template:
            logger.error("Template are not the same on source and target stackset.")
            exit_code=1
        
        # check if parameters are set to the same values
        if source_stack_set.parameters != target_stack_set.parameters:
            logger.error("Parameters are not the same on source and target stackset.")
            exit_code=1

        # check if there isn't already deployed instance in the target stack for the account and region
        conflict_instances = []
        for instance in source_stack_set.filtered_instances:
            if instance_already_exist(instance,target_stack_set.instances):  
                conflict_instances.append(instance)    
        if conflict_instances:  
            logger.error("Stack instances for the OU or accounts in the OU already exists in the target stackset. Please fix first.")
            for instance in conflict_instances:
                logger.info(instance)
            exit_code=1

    # Check if a change set will be triggered by migrating   
    if target_stack_set and detect_change_set:
        change_set = []
        for i in source_stackset.instances:
            if evaluate_change_set(i, target_stackset) > 0:
                change_set.append(i)
        if len(change_set)>0:
            logger.error("ChangeSet identified changes. Please review to the following stacks to review the change.")
            logger.error("ChangeSet should be DELETED after review, otherwise it will cause a drift")
            for i in change_set:
                logger.error(i)
            exit_code=1

    if exit_code == 1:
        logger.error("A comparison failed. Exiting now. Please review log and reports file")
        sys.exit(exit_code)


def setup_logging(logger, log_level):
    logger.setLevel(logging.getLevelName(log_level))
    # create file handler which logs even debug messages
    fh = logging.FileHandler(f"logs/migrate_stackset_{args.source_stack_set_name}.log")
    fh.setLevel(logging.INFO)
    # create console handler with a higher log level
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    # create formatter and add it to the handlers
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - [%(levelname)s] - %(message)s"
    )
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)
    # add the handlers to the logger
    logger.addHandler(fh)
    logger.addHandler(ch)


if __name__ == "__main__":

    args = setup_args()

    # Setup logging to output and file
    # create logger with 'spam_application'
    logger = logging.getLogger(f"STACKSET {args.source_stack_set_name}")
    setup_logging(logger, 'INFO')

    accounts = []
    # If organizational unit is not defined do not try to load the associated child accounts
    if args.organizational_unit:
        if not args.organizational_unit.startswith("ou-"):
            logger.error("Invalid OU id. It should start with ou-")
            sys.exit(1)
        accounts = get_accounts_from_ou(session, args.organizational_unit)

    # Loading the target stack if provided
    if args.target_stack_set_name:
        target_stackset = StackSet(args.target_stack_set_name)
        target_stackset.load([])
    else:
        target_stackset = None
        logger.info("Evaluating source stackset only")

    # Loading the stack set and performs checks such as regions, templates, parameters, drift...
    source_stackset = StackSet(args.source_stack_set_name)
    source_stackset.load(accounts)
    logger.info(
        f"The stackset {source_stackset.name} has {len(source_stackset.instances)} stack instances."
    )
    if not args.disable_drift:
        source_stackset.detect_drift()
        
    source_stackset.evaluate_stack_sync()
    source_stackset.generate_reports()

    # Check if the stackset is deployed for the OU
    if len(source_stackset.filtered_instances) == 0 and args.organizational_unit:
        logger.error("This stackset is not deployed in this account or OU")
        sys.exit(1)
    else:
        # Runs on all instances
        source_stackset.filtered_instances = source_stackset.instances

    compare_stack_sets(source_stackset,target_stackset, args.change_set)

    # Exit if there is not target stack to migrate to.
    if not args.target_stack_set_name:
        logger.info(
            f"Stackset {args.source_stack_set_name} looks good to go for a migration."
        )
        sys.exit(0)

    logger.info(
        f"Ready to move {len(source_stackset.filtered_instances)} stack instances for {len(accounts)} accounts to {target_stackset.name}."
    )

    check = input(
        f"Deleting stack instances from stackset {args.source_stack_set_name} for ou {args.organizational_unit}. Are you sure ? (Y/N): "
    )
    if check != "Y":
        logger.info("Aborting now.")
        sys.exit(1)
    if not args.organizational_unit:
        ou = source_stackset.ous
    else:
        ou = [args.organizational_unit]
    source_stackset.delete_stack_instances(ou)
    target_stackset.import_stack(source_stackset.filtered_instances)
    logger.info(
        "Migration complete. Please check the status of stack instances on the target stackset"
    )
