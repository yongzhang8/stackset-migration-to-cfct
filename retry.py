import boto3
import time
import logging

# create logger with 'spam_application'
logger = logging.getLogger('migrate_stackset_script')
logger.setLevel(logging.INFO)
# create file handler which logs even debug messages
fh = logging.FileHandler('migrate_stackset.retry.log')
fh.setLevel(logging.INFO)
# create console handler with a higher log level
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
# create formatter and add it to the handlers
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
ch.setFormatter(formatter)
# add the handlers to the logger
logger.addHandler(fh)
logger.addHandler(ch)

with open('cve-debug-instances-deleted.txt') as f:
    instances = f.read().splitlines()

client = boto3.client('cloudformation')
def wait_operation_is_complete(stackset_name, operation_id):
        """Simple waiter for cloudformation stackset operation"""

        client = boto3.client("cloudformation")
        response = client.describe_stack_set_operation(
            StackSetName=stackset_name, OperationId=operation_id
        )
        while not response["StackSetOperation"]["Status"] in ["FAILED", "SUCCEEDED"]:
            time.sleep(5)
            response = client.describe_stack_set_operation(
                StackSetName=stackset_name, OperationId=operation_id
            )
            logger.info(
                f"The {response['StackSetOperation']['Action']} Operation id {operation_id} \
    has status {response['StackSetOperation']['Status']}"
            )

def import_stack(stackset_name, instances):
    """Impport stack instances into a stackset."""
    logger.info(f"Starting to migrate {len(instances)} instances into {stackset_name}")
    client = boto3.client("cloudformation")
    for i in range(0, len(instances), 10):
        logger.info(f"Import stack instances from {i} to {i+10}")
        response = client.import_stacks_to_stack_set(
            StackSetName=stackset_name,
            StackIds=instances[i : i + 10],
            OperationPreferences={
                "RegionConcurrencyType": "PARALLEL",
                "FailureToleranceCount": 10,
            },
        )
        wait_operation_is_complete(stackset_name, response["OperationId"])

import_stack('cfct-debug', instances)