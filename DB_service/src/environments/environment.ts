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
    host: "db",
    name: requireEnv("POSTGRES_DB"),
    password: requireEnv("POSTGRES_PASSWORD"),
    port: 5432,
  },
};