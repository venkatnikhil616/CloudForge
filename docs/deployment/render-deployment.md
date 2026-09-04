# 1-Click Deployment Guide for Render.com

This guide explains how to deploy CloudTask to **Render** completely free for your college project submission.

---

## Prerequisites
1. Free [GitHub](https://github.com) account with this repository pushed: `venkatnikhil616/CloudForge`.
2. Free [Render](https://render.com) account.
3. Free [CloudAMQP](https://customer.cloudamqp.com/signup) account (free RabbitMQ shared broker, takes 30 seconds).

---

## Method 1: 1-Click Render Blueprint (Recommended)

1. Go to your [Render Dashboard](https://dashboard.render.com/).
2. Click **New +** $\to$ **Blueprint**.
3. Select your repository: `venkatnikhil616/CloudForge`.
4. Render will read [`render.yaml`](file:///home/kali/CloudForge/render.yaml) and automatically create:
   - **PostgreSQL Database** (`cloudtask-db`) — Free Tier
   - **Redis Instance** (`cloudtask-redis`) — Free Tier
   - **Web Service** (`cloudtask-platform`) — Free Tier
5. When prompted for `RABBITMQ_URL`, paste your free **CloudAMQP URL** (e.g. `amqps://user:pass@cow.rmq2.cloudamqp.com/vhost`).
6. Click **Apply**.

Render will automatically build the container and provide your live HTTPS URL:
```
https://cloudtask-platform.onrender.com
```

---

## Method 2: Manual Web Service Setup on Render

If you prefer setting up via the Render UI:
1. **Create PostgreSQL**: Click **New +** $\to$ **PostgreSQL** (Name: `cloudtask-db`, Plan: Free). Copy the `Internal Database URL`.
2. **Create Redis**: Click **New +** $\to$ **Redis** (Name: `cloudtask-redis`, Plan: Free). Copy the `Internal Redis URL`.
3. **Create Web Service**:
   - Click **New +** $\to$ **Web Service**.
   - Connect repository `venkatnikhil616/CloudForge`.
   - **Runtime**: Docker
   - **Dockerfile Path**: `Dockerfile.render`
   - **Plan**: Free
   - **Environment Variables**:
     - `DATABASE_URL` = `<Internal Database URL>`
     - `REDIS_URL` = `<Internal Redis URL>`
     - `RABBITMQ_URL` = `<Your CloudAMQP URL>`
     - `JWT_SECRET_KEY` = `<any secure random 32 character string>`
4. Click **Deploy Web Service**.

---

## Testing Your Live Deployment

Open your browser or terminal and test:
```bash
# Health Check
curl https://<your-subdomain>.onrender.com/health/live

# Interactive OpenAPI Documentation
https://<your-subdomain>.onrender.com/docs
```
