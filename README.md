# Stackset migration to CfCT

This projects aims at automating the migration of stack instances from one AWS CloudFormation StackSets to another.  
A typical use case is when a customer starts using Customization for Control Tower and want to migrate existing AWS CloudFormation StackSets under the management of Customization for Control Tower. Another use case is when a customer wants to migrate from SERVICE_MANAGED StackSets to SELF_MANAGED StackSets.

Link to a demo video [here](https://broadcast-gamma.amazon.com/videos/429723)

This script automates most of the steps to migrate stack instances.

Sample usages:
- Migrates all stack instances to a new StackSet
```bash
python3 migrate.py -s source_stack_set_name -t target_stack_set_name
```

- Migrates all stack instances belonging to on Organizational Unit Id
```bash
python3 migrate.py -s source_stack_set_name -t target_stack_set_name -o ou_id_to_migrate
```

- Validates the status of an existing stackset prior to migration
```bash
python3 migrate.py -s source_stack_set_name
```

- Validate the status of every service_managed stackset
```
for i in `aws cloudformation list-stack-sets --query "Summaries[?PermissionModel=='SERVICE_MANAGED'&&Status=='ACTIVE'].StackSetName" --output text`; do
    python3 migrate.py -s $i
done
```

Arguments
```bash
usage: migrate.py [-h] -s SOURCE_STACK_SET_NAME [-t TARGET_STACK_SET_NAME] [-o ORGANIZATIONAL_UNIT] [-d]

optional arguments:
  -h, --help            show this help message and exit
  -s SOURCE_STACK_SET_NAME, --source-stack-set-name SOURCE_STACK_SET_NAME
                        Source stack set name
  -t TARGET_STACK_SET_NAME, --target-stack-set-name TARGET_STACK_SET_NAME
                        Target stack set name
  -o ORGANIZATIONAL_UNIT, --organizational-unit ORGANIZATIONAL_UNIT
                        Organizational Unit to migrate
  -d, --disable-drift   Disable drift detection. However script still checks for drift to be IN-SYNC
  -c, --enable-change-set Connect to each stack instances and create a change set to confirm that template are the same

```

The tool generates reports in the logs in the logs folder and reports in the reports folder.
The reports can be aggregated by going into the reports folder and running the following command:
```
python3 ../generate_csv.py
```
It will produce a CSV output with stats for each StackSet.

## 0. Limitations
* This automation cannot be used when the AWS CloudFormation StackSets is applied to an OU with nested OU
* When using this tool with Customization for Control Tower (CfCT) double checks that the CfCT manifest is aligned with the migration to avoid stack instances deletion


## 1. Overall process
The following steps outline the process to migrate one Stack instance from one StackSet to another:
1. Validating that the template and parameters are identical between source and destination.
2. Verify that there is no drift on the stack instances
3. Removing the stack instance from the source StackSet (delete and retain)
4. Import the stack instance into the target StackSet (import)


## 2. Specific process for Customization for Control Tower
The way CfCT deploys StackSets to AWS Accounts is by performing a difference analysis between AWS accounts having the StackSet deployed and AWS accounts in the manifest.yml file. As a cautionary step, it is recommended to not deploy the pipeline while migrating StackSets.
CfCT has also some specifics about rules to create/update/delete StackSet:
1. When adding a StackSet in the manifest to no OU or an empty OU, the StackSet will be created by not applied. It means that no create/update/delete will happen to the Stack instances
2. If the manifest contains less AWS accounts than the AWS accounts that currently have the StackSet, CfCT will delete the Stack instances for these accounts

Here is the recommended approach to avoid any risk:
1. Deploy the new StackSet with CfCT with no target OU or an empty target OU
2. Migrate the OUs into the new StackSet using the automation tool
3. Update the StackSet manually to be sure they are not outdated and align on the Tags
4. Add the OUs or accounts as well as AWS Regions into the manifest file
5. **triple check** the manifest file compared with Accounts currently deployed.
5. Deploy the new manifest and let the pipeline update the StackSet.
The StackSet is now managed by CfCT. If an OU is removed from the manifest, CfCT will delete the Stack instances.

Some tips:
1. Move all OUs then update the manifest file. Do not move OU by OU otherwise you increase the risk of having a mismatch between the manifest and Stack instances causing the extra instances to be created/deleted (depending on if the OU is added too early or too late)
2. Triple check the templates and parameters. Once CfCT will manage the StackSet it might be updated at every deployment. CfCT template will prevail.

# Known limitations
1. The tool relies on string comparison to evaluate the difference between source template and target template. It is very flakky as a carrier return at the end of file with break the comparison. Recommended approach is to remove this feature and rely on changeset only. Changeset will also guarantee that all instances are reachable by AWSControlTowerExecution role.
2. No retry mechanism built for now. If the import failed after the deletion occured, all Stack instances will be in the wild (but not lost). The tool dumps a file containing the list of Stack instances deleted. It can be used in conjunction with the retry.py file to retry the import.