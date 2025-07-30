import { z } from 'zod'
import * as fs from 'fs';
export const BotConfigSchema = z.object({
  platform: z.enum(["google_meet", "zoom", "teams", "google_okta"]),
  meetingUrl: z.string().url(),
  botName: z.string(),
  token: z.string(),
  connectionId: z.string(),
  automaticLeave: z.object({
    waitingRoomTimeout: z.number().int(),
    noOneJoinedTimeout: z.number().int(),
    everyoneLeftTimeout: z.number().int()
  })
});

export const loadConfig = (configPath: string) => {
  const configData = fs.readFileSync(configPath, 'utf-8');
  return BotConfigSchema.safeParse(JSON.parse(configData))
}
