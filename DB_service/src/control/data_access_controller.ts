import { Pool } from "pg";
import { environment } from "../environments/environment";

const { user, host, name: database, password, port } = environment.db;

export class DataAccessController {
  static pool = new Pool({
    user,
    host,
    database,
    password,
    port,
  });
}
