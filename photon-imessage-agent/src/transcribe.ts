/** ElevenLabs Scribe for inbound iMessage audio. Transcript is never logged. */

const STT_URL = "https://api.elevenlabs.io/v1/speech-to-text";

export async function transcribeAudio(blob: Blob, filename: string, apiKey: string): Promise<string> {
  const key = apiKey.trim();
  if (!key) throw new Error("Missing ELEVENLABS_API_KEY.");
  const body = new FormData();
  body.append("model_id", "scribe_v1");
  body.append("language_code", "eng");
  body.append("file", blob, filename || "voice.m4a");
  const response = await fetch(STT_URL, {
    method: "POST",
    headers: { "xi-api-key": key },
    body,
  });
  if (!response.ok) throw new Error(`ElevenLabs speech-to-text HTTP ${response.status}`);
  const payload = await response.json() as { text?: unknown };
  const text = typeof payload.text === "string" ? payload.text.trim() : "";
  if (!text) throw new Error("ElevenLabs returned an empty transcript.");
  return text;
}
