import dotenv from "dotenv";
import path from "path";

// .env einmal, kontrolliert, stabil laden
if (!process.env.POSTGRES_USER) {
  dotenv.config({ path: path.resolve(__dirname, "../../../.env.local") });
}

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

export const environment = {
  db: {
    user: requireEnv("POSTGRES_USER"),
    host: process.env.POSTGRES_HOST ?? "localhost",
    name: requireEnv("POSTGRES_DB"),
    password: requireEnv("POSTGRES_PASSWORD"),
    port: Number(process.env.POSTGRES_PORT ?? 5432),
  },
};
