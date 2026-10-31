# Firebase Setup Guide

## The Error You're Seeing
The `auth/configuration-not-found` error means that either:
1. The Firebase project doesn't exist
2. Email/Password authentication is not enabled in your Firebase project
3. The configuration credentials are incorrect

## How to Fix This

### Step 1: Create a Firebase Project
1. Go to [Firebase Console](https://console.firebase.google.com/)
2. Click "Create a project" or "Add project"
3. Enter a project name (e.g., "tansam-dashboard")
4. Follow the setup wizard

### Step 2: Enable Authentication
1. In your Firebase project, go to **Authentication** in the left sidebar
2. Click **Get started**
3. Go to the **Sign-in method** tab
4. Enable **Email/Password** authentication
5. Click **Save**

### Step 3: Get Your Configuration
1. Go to **Project Settings** (gear icon)
2. Scroll down to **Your apps** section
3. Click **Add app** > **Web app** (</>) icon
4. Register your app with a nickname
5. Copy the `firebaseConfig` object

### Step 4: Update Your Configuration
Copy `.env.example` to `.env` and fill in the values from the `firebaseConfig` object
(`src/firebase.js` reads them; restart `npm run dev` after editing `.env`):

```bash
VITE_FIREBASE_API_KEY=your-actual-api-key
VITE_FIREBASE_AUTH_DOMAIN=your-project-id.firebaseapp.com
VITE_FIREBASE_PROJECT_ID=your-project-id
VITE_FIREBASE_STORAGE_BUCKET=your-project-id.firebasestorage.app
VITE_FIREBASE_MESSAGING_SENDER_ID=your-messaging-sender-id
VITE_FIREBASE_APP_ID=your-app-id
```

### Step 5: Enable Firestore and deploy the security rules
1. Go to **Firestore Database** in the left sidebar
2. Click **Create database** and choose **Start in production mode**
3. Select a location for your database
4. Deploy `firestore.rules` (each user can only read/write `users/{their uid}/**`):

```bash
npx firebase-tools login
npx firebase-tools deploy --only firestore:rules --project your-project-id
```

## Alternative: Use Demo Mode
If you want to test the app without setting up Firebase, set `VITE_DEMO_MODE=true` in `.env`.
Any email/password then logs in and data stays in the browser's localStorage.
