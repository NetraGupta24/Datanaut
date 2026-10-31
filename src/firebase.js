import { initializeApp } from "firebase/app";
import { getAuth } from "firebase/auth";
import { getFirestore } from "firebase/firestore";

const firebaseConfig = {
  apiKey: "AIzaSyATuyb3gOdjYY13luQVTFlzKVd1iADeavk",
  authDomain: "datanaut-3f632.firebaseapp.com",
  projectId: "datanaut-3f632",
  storageBucket: "datanaut-3f632.firebasestorage.app",
  messagingSenderId: "175440237356",
  appId: "1:175440237356:web:17c841b3d63cf73ae39289"
};

const app = initializeApp(firebaseConfig);

export const auth = getAuth(app);
export const db = getFirestore(app);
// Demo mode (any email/password works, nothing is saved to Firebase) only when explicitly enabled
export const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === "true";
export default app;