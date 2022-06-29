import boto3
from botocore.exceptions import ClientError
import logging

logger = logging.getLogger("__utils__")


def assume_role(account_id, role_name, region):
    """
    Assumes the provided role in the provided account id and returns a session object
    :param account_id: AWS Account Number
    :param role_name: Role to assume in target account
    :param region: AWS Region for the Client call
    :return: Session object for the specified AWS Account and Region
    """
    try:
        sts_client = boto3.client(
            "sts",
            region_name=region,
            endpoint_url=f"https://sts.{region}.amazonaws.com",
        )
        partition = sts_client.get_caller_identity()["Arn"].split(":")[1]
        response = sts_client.assume_role(
            RoleArn="arn:{}:iam::{}:role/{}".format(partition, account_id, role_name),
            RoleSessionName=str(account_id + "-" + role_name),
            # SessionPolicy=json.dumps({
            # })
        )
        sts_session = boto3.Session(
            aws_access_key_id=response["Credentials"]["AccessKeyId"],
            aws_secret_access_key=response["Credentials"]["SecretAccessKey"],
            aws_session_token=response["Credentials"]["SessionToken"],
            region_name=region,
        )
        logger.info(
            "Assumed session for {} - used role: {} - region {}.".format(
                account_id, role_name, region
            )
        )
        return sts_session
    except Exception:
        raise Exception(f"Could not assume role in account {account_id}")


def get_all_accounts(session):
    _accounts = []
    client = session.client("organizations")
    paginator = client.get_paginator("list_accounts")
    for page in paginator.paginate():
        _accounts.extend(a["Id"] for a in page["Accounts"])
    return _accounts


def get_accounts_from_ou(session, organizational_unit: str):
    """Return the list of accounts belonging to one OU"""
    client = session.client("organizations")
    _accounts = []
    paginator = client.get_paginator("list_accounts_for_parent")
    operation_parameters = {"ParentId": organizational_unit}
    page_iterator = paginator.paginate(**operation_parameters)
    try:
        for page in page_iterator:  # Suspended accounts ?
            _accounts.extend([a["Id"] for a in page["Accounts"]])
        ou_paginator = client.get_paginator("list_children")
        operation_parameters = {
            "ParentId": organizational_unit,
            "ChildType": "ORGANIZATIONAL_UNIT",
        }
        ou_page_iterator = ou_paginator.paginate(**operation_parameters)
        for page in ou_page_iterator:  # Suspended accounts ?
            for ou in page["Children"]:
                _accounts.extend(get_accounts_from_ou(ou["Id"]))
    except ClientError as e:
        if e.response["Error"]["Code"] != "ParentNotFoundException":
            raise e

    # logger.info(f"Found {len(_accounts)} in OU {organizational_unit}")

    return _accounts