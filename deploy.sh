set -e

docker build --platform linux/amd64 --target prod -t gcr.io/bzan-coding-server/codingserver:prod .

docker push gcr.io/bzan-coding-server/codingserver:prod

gcloud run deploy codingserver \
  --image gcr.io/bzan-coding-server/codingserver:prod \
  --region us-central1 --platform managed \
  --env-vars-file env.yaml \
  --service-account bzancodingserver-sa@bzan-coding-server.iam.gserviceaccount.com