# Backend Deployment Guide: Render.com

Follow these steps to deploy your Flask Attendance API with Face Recognition on Render.

## 1. Prerequisites
- A **GitHub** account with your code pushed to a repository.
- A **Render.com** account.
- An active **MySQL Database** (connected via Host, User, Password, Port). You can use **Aiven**, **PlanetScale**, or Render's Managed MySQL.

## 2. Prepare Your Repository
Ensure your `backend/` folder contains:
- `app.py`
- `database.py`
- `face_utils.py`
- `requirements.txt`
- `Procfile` (with content: `web: gunicorn app:app`)

## 3. Create Web Service on Render
1.  Log in to [Render Dashboard](https://dashboard.render.com).
2.  Click **New +** and select **Web Service**.
3.  Connect your GitHub/GitLab repository.
4.  Select the repository for the Attendance System.

## 4. Configure Build & Runtime Settings
Set the following options in the Render creation form:
- **Name**: `attendify-backend` (or any name)
- **Environment**: `Python 3`
- **Region**: Choose the one closest to you (e.g., Singapore or Frankfurt).
- **Branch**: `main` (or your primary branch)
- **Root Directory**: `backend` (CRITICAL: Since the app is in a subfolder)
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `gunicorn app:app`

## 5. Set Environment Variables
Go to the **Environment** tab on Render and add the following keys:
| Key | Value |
| :--- | :--- |
| `DB_HOST` | Your MySQL Host (e.g., `mysql.aivencloud.com`) |
| `DB_USER` | Your MySQL Username |
| `DB_PASSWORD` | Your MySQL Password |
| `DB_NAME` | Your MySQL Database Name |
| `DB_PORT` | `3306` (or your DB port) |
| `SECRET_KEY` | A random long string for JWT security |
| `PYTHON_VERSION` | `3.9.0` (or higher) |

> [!IMPORTANT]
> **Performance Note**: The `face_recognition` library (dlib) is heavy. On Render's free tier, the first build might take several minutes. If it fails due to memory, you may need to upgrade to a 'Starter' instance ($7/mo) or ensure you use `opencv-python-headless` in `requirements.txt` to save space.

## 6. Update Frontend API URL
Once the service is deployed, Render will provide a URL (e.g., `https://attendify-backend.onrender.com`).
1.  Open `frontend/assets/js/api.js` in your local code.
2.  Update `API_BASE_URL` to your Render URL:
    ```javascript
    const API_BASE_URL = 'https://attendify-backend.onrender.com/api';
    ```
3.  Redeploy/Update your Frontend on Vercel.

## 7. Verify Deployment
- Check the **Events** tab on Render for "Deployment successful".
- Visit `https://your-app.onrender.com/api/subjects` (it should return `{"message": "Token is missing!"}` if working correctly).
