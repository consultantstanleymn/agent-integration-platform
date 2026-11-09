# What emulators cannot prove

The cloud tests use moto, an AWS emulator. A LocalStack profile exists in `docker-compose.yml`. It is authored and was not run. An emulator checks that the code calls the API in a valid way. It does not check how real AWS behaves. This table lists the gaps and how the owner can close each one on real AWS with `scripts/aws_verify.py`.

| Topic | What an emulator cannot prove | How to prove it on real AWS |
|---|---|---|
| IAM enforcement | Moto accepts calls that real IAM would deny. The policy is only read as text by the verifier | Create the IAM user from `infra/aws-engagement`. Call S3, SQS and Secrets Manager with that user. Try one call outside the three resources. It must fail with AccessDenied |
| Bucket policy and public access at the edge | An emulator stores the public access block. It does not serve requests that the block would refuse | Request an object by its public URL without credentials. The answer must be 403 |
| Visibility timeout and redrive timing | Moto moves a message to the DLQ by receive count. Real timing depends on the visibility timeout, polling and the service clock | Send one poison message. Receive it three times with the real timeout. Watch it arrive in the DLQ. Record the time |
| Secrets Manager resource policies and KMS | Moto does not enforce resource policies or key permissions | Read the secret with a user that has no access. It must fail. Check the key policy if a customer key is used |
| Throttling and limits | An emulator has no real rate limits, quotas or message size limits | Send messages near the size limit and at a high rate. Record the errors |
| Cost | An emulator has no bill | Check the budget alert in the console. Read the cost explorer after one day |

## What the emulator run does prove

- The adapters call S3, SQS and Secrets Manager with valid arguments.
- The queue code handles receive counts and a redrive.
- The verifier and teardown scripts work from start to end, and the teardown leaves nothing behind.

## Moto behavior seen in tests

Moto moved a message to the DLQ after the receive count passed `maxReceiveCount` when the visibility timeout was 0. The adapter tests assert that result. No fake result is used.

## LocalStack profile

The service `localstack` in `docker-compose.yml` uses the profile `localstack`. It listens on `127.0.0.1:25166`. It was not started. Authored, not run: the compose file is syntax-checked only. Reason: the image was not pulled, by owner decision.
