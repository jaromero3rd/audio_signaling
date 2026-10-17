export class SetupError extends Error {}

export type Config = {
  key: string;
  project: string;
  sender: string;
  agent: string;
  assignment: string;
};

export function configuration(env: Record<string, string | undefined> = process.env): Config {
  const required = (name: string) => {
    const value = env[name]?.trim();
    if (!value) throw new SetupError(`Missing ${name}.`);
    return value;
  };
  const config = {
    key: required("PHOTON_API_KEY"),
    project: required("PHOTON_PROJECT_ID"),
    sender: required("PHOTON_TEST_SENDER"),
    agent: required("PHOTON_AGENT_HANDLE"),
    assignment: required("PHOTON_ASSIGNMENT_ID"),
  };
  if (!/^pho_prj_[0-7][0-9a-hjkmnp-tv-z]{25}$/.test(config.project)) {
    throw new SetupError("PHOTON_PROJECT_ID must identify a Photon 2.0 Beta project.");
  }
  for (const value of [config.sender, config.agent]) {
    if (!/^\+[1-9]\d{7,14}$/.test(value)) throw new SetupError("Use E.164 iMessage phone numbers.");
  }
  if (config.sender === config.agent) throw new SetupError("Sender and agent must be different.");
  return config;
}
