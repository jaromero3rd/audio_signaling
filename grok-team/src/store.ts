import { Database } from "bun:sqlite";
import { chmodSync } from "node:fs";
import type { Role } from "./config.ts";

export type Job = {
  id: string;
  kind: "ask" | "ship";
  target: string;
  input: string;
  state: "queued" | "running" | "done" | "failed";
  output: string;
};
export type Announcement = {
  id: string; guild: string; channel: string; created: number;
  content: string; url: string;
};

export class Store {
  db: Database;
  constructor(path: string) {
    this.db = new Database(path, { create: true, strict: true });
    if (path !== ":memory:") chmodSync(path, 0o600);
    this.db.exec(`
      PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL,
        input TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
        output TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY, role TEXT NOT NULL, input TEXT NOT NULL,
        output TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS published (
        branch TEXT PRIMARY KEY, sha TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS announcements (
        id TEXT PRIMARY KEY, guild TEXT NOT NULL, channel TEXT NOT NULL,
        created INTEGER NOT NULL, content TEXT NOT NULL, url TEXT NOT NULL
      );
    `);
  }
  enqueue(kind: Job["kind"], target: string, input: string, id: string = crypto.randomUUID()) {
    this.db.query("INSERT OR IGNORE INTO jobs (id,kind,target,input,created) VALUES (?,?,?,?,?)")
      .run(id, kind, target, input, Date.now());
    return id;
  }
  claim(): Job | null {
    return this.db.query<Job, []>(`
      UPDATE jobs SET state='running' WHERE id=(
        SELECT id FROM jobs WHERE state='queued' ORDER BY created,id LIMIT 1
      ) RETURNING *
    `).get();
  }
  finish(id: string, output: string, failed = false) {
    this.db.query("UPDATE jobs SET state=?,output=? WHERE id=?")
      .run(failed ? "failed" : "done", output, id);
  }
  get(id: string) { return this.db.query<Job, [string]>("SELECT * FROM jobs WHERE id=?").get(id); }
  recent() { return this.db.query<Job, []>("SELECT * FROM jobs ORDER BY created DESC LIMIT 10").all(); }
  remember(role: Role, input: string, output: string) {
    this.db.query("INSERT INTO history (role,input,output) VALUES (?,?,?)").run(role, input, output);
  }
  context() {
    return this.db.query<{ role: Role; input: string; output: string }, []>(
      "SELECT role,input,output FROM history ORDER BY id DESC LIMIT 10",
    ).all().reverse();
  }
  lastPublished(branch: string) {
    return this.db.query<{ sha: string }, [string]>("SELECT sha FROM published WHERE branch=?").get(branch)?.sha;
  }
  announcement(message: Announcement) {
    this.db.query(`INSERT INTO announcements (id,guild,channel,created,content,url) VALUES (?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET content=excluded.content`)
      .run(message.id, message.guild, message.channel, message.created, message.content, message.url);
  }
  announcements() {
    return this.db.query<Announcement, []>("SELECT * FROM announcements ORDER BY created DESC,id DESC LIMIT 10").all();
  }
  published(branch: string, sha: string) {
    this.db.query("INSERT INTO published (branch,sha) VALUES (?,?) ON CONFLICT(branch) DO UPDATE SET sha=excluded.sha")
      .run(branch, sha);
  }
  interrupt() {
    this.db.query("UPDATE jobs SET state='failed',output='Worker interrupted; submit this job again.' WHERE state='running'").run();
  }
  close() { this.db.close(); }
}
