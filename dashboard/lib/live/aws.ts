// AWS clients for the live routes. Credentials come from Vercel's OIDC
// token, exchanged by STS for one-hour credentials of the nightshift-dashboard
// role (terraform/dashboard.tf). No access key exists anywhere.
//
// The provider fetches the token when a request needs credentials, not when
// this module loads: the token only exists inside a request.

import { CloudWatchClient } from "@aws-sdk/client-cloudwatch";
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { LambdaClient } from "@aws-sdk/client-lambda";
import { awsCredentialsProvider } from "@vercel/oidc-aws-credentials-provider";

export type Clients = {
  cw: CloudWatchClient;
  ddb: DynamoDBClient;
  lam: LambdaClient;
};

let clients: Clients | undefined;

export function awsClients(): Clients {
  if (clients) return clients;
  const roleArn = process.env.AWS_ROLE_ARN;
  if (!roleArn) throw new Error("AWS_ROLE_ARN is not set");
  // Set explicitly: Vercel sets AWS_REGION to the function's own region.
  const region = process.env.AWS_REGION ?? "ca-central-1";
  const credentials = awsCredentialsProvider({ roleArn });
  clients = {
    cw: new CloudWatchClient({ region, credentials }),
    ddb: new DynamoDBClient({ region, credentials }),
    lam: new LambdaClient({ region, credentials }),
  };
  return clients;
}
