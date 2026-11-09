# AWS resources for the agent platform (written, not applied)

This folder holds Terraform code for one S3 bucket, an SQS queue with a dead-letter queue, one secret, one IAM user with a narrow policy, and a monthly cost budget.

Authored, not run: this file is written and syntax-checked with python-hcl2. It was not executed against AWS. Reason: no terraform or tofu binary is installed, and there is no cloud account by design.

The code is plain Terraform syntax. OpenTofu also accepts it. No Terraform or OpenTofu binary is installed on the author machine, so `terraform validate` was never run.

## What the static tests check

`tests/test_infra_hcl.py` parses every `.tf` file and checks these facts:

- the bucket blocks all four kinds of public access,
- the main queue has a redrive policy that points to the dead-letter queue,
- the IAM policy documents have no `*` in actions or resources,
- a budget exists with an 80 percent notification.

## If the owner ever applies it

Follow this order. Stop at any step that looks wrong.

1. Create a new AWS account or use a sandbox account. Do not use a production account.
2. Set a budget alert in the console first, by hand.
3. Create a least-privilege IAM user for Terraform itself.
4. Set `alert_email` and a unique `name_prefix`. Run `terraform init`, then `terraform plan`. Read the plan.
5. Run `terraform apply`. Then run `scripts/aws_verify.py` against the real region.
6. Put the LLM key into the secret by hand. The value never goes into the code.
7. When done, run `scripts/aws_teardown.py --yes`, then `scripts/aws_verify.py --expect-empty`, then `terraform destroy`.
