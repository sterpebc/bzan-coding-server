Docker build & run instructions
===============================

This project provides a single `Dockerfile` with `base`, `dev`, and `prod` stages and an `entrypoint.sh` script that chooses the startup command based on the `MODE` environment variable.

Build the images

- Build a development image (optional):

```bash
docker build --target dev -t codingserver:dev .
```

- Build a production image (recommended for Cloud Run):

```bash
docker build --target prod -t codingserver:prod .
```

Run locally

- Run the production image locally (uses `gunicorn`):

```bash
docker run -p 8080:8080 codingserver:prod
```

- Run the production image but use dev mode (hot start with the dev server):

```bash
docker run -e MODE=dev -p 8080:8080 codingserver:prod
```

- Override the entrypoint to run the dev server directly:

```bash
docker run --entrypoint python -p 8080:8080 codingserver:prod app.py
```

Cloud Run deployment

- Build and push the `prod` image and deploy from the pushed image. Example with Google Container Registry:

```bash
docker build --target prod -t gcr.io/PROJECT-ID/codingserver:prod .
docker push gcr.io/PROJECT-ID/codingserver:prod
gcloud run deploy codingserver --image gcr.io/PROJECT-ID/codingserver:prod --region YOUR_REGION --platform managed
```

Notes
- The `entrypoint.sh` will prefer an explicit command if provided (e.g. `docker run image python app.py`).
- `MODE=dev` will run `python app.py` (Flask dev server). Default is production `gunicorn` server.
- On Apple Silicon / local builds with Cloud Code, you may need QEMU support for cross-platform builds:
  ```bash
docker run --privileged --rm tonistiigi/binfmt --install all
``` 
- For the current `gunicorn==20.1.0` workflow, the image is pinned to a setuptools version that provides `pkg_resources`, which is required by Gunicorn on startup.
