import { initializeApp } from "firebase/app";
import { getAuth } from "firebase/auth";
import { getFirestore } from "firebase/firestore";

// Demo mode (any email/password works, nothing is saved to Firebase) only when explicitly enabled
export const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === "true";

// Web config comes from .env (see .env.example). These values are public client
// identifiers; access control is enforced by firestore.rules.
const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID
};

const REQUIRED = {
  apiKey: "VITE_FIREBASE_API_KEY",
  authDomain: "VITE_FIREBASE_AUTH_DOMAIN",
  projectId: "VITE_FIREBASE_PROJECT_ID",
  appId: "VITE_FIREBASE_APP_ID"
};
const missing = Object.entries(REQUIRED)
  .filter(([field]) => !firebaseConfig[field])
  .map(([, envName]) => envName);

if (missing.length && !DEMO_MODE) {
  throw new Error(
    `Missing Firebase config: ${missing.join(", ")}. Copy .env.example to .env, fill it in, and restart Vite.`
  );
}

// In demo mode without a config, run with no Firebase at all (auth/db are null)
const app = missing.length ? null : initializeApp(firebaseConfig);

export const auth = app ? getAuth(app) : null;
export const db = app ? getFirestore(app) : null;
export default app;
