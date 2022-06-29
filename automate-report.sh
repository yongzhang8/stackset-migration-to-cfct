#!/bin/bash
#  © 2021 Amazon Web Services, Inc. or its affiliates. All Rights Reserved.
#  This AWS Content is provided subject to the terms of the AWS Customer Agreement available at
#  http://aws.amazon.com/agreement or other written agreement between Customer and either
#  Amazon Web Services, Inc. or Amazon Web Services EMEA SARL or both.
#  The sample code; software libraries; command line tools; proofs of concept; templates; or other 
#  related technology (including any of the foregoing that are provided by our personnel) is provided 
#  to you as AWS Content under the AWS Customer Agreement, or the relevant written agreement between 
#  you and AWS (whichever applies). You should not use this AWS Content in your production accounts, 
#  or on production or other critical data. You are responsible for testing, securing, and optimizing 
#  the AWS Content, such as sample code, as appropriate for production grade use based on your specific 
#  quality control practices and standards. Deploying AWS Content may incur AWS charges for creating or 
#  using AWS chargeable resources, such as running Amazon EC2 instances or using Amazon S3 storage.

if ! command -v aws &> /dev/null
then
    echo "This script requires aws cli to work."
    exit 1
fi

#Looping over each ACTIVE SERVICE_MANAGED stackset.
for i in `aws cloudformation list-stack-sets --query "Summaries[?PermissionModel=='SERVICE_MANAGED'&&Status=='ACTIVE'].StackSetName" --output text`; do
    python3 main.py -s $i -d &
    pids[$i] = $!
done

# wait for all pids
for pid in ${pids[*]}; do
    wait $pid
done