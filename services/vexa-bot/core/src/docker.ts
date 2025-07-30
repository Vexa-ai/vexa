import { runBot } from "."
import { z } from 'zod';
import { BotConfig } from "./types"; // Import the BotConfig type

// Define a schema that matches your JSON configuration
export const BotConfigSchema = z.object({
  platform: z.enum(["google_meet", "zoom", "teams", "google_okta"]),
  meetingUrl: z.string().url().nullable(), // Allow null from BOT_CONFIG
  botName: z.string(),
  token: z.string(),
  connectionId: z.string(),
  nativeMeetingId: z.string(), // *** ADDED schema field ***
  language: z.string().nullish(), // Optional language
  task: z.string().nullish(),     // Optional task
  redisUrl: z.string(),         // Required Redis URL
  automaticLeave: z.object({
    waitingRoomTimeout: z.number().int(),
    noOneJoinedTimeout: z.number().int(),
    everyoneLeftTimeout: z.number().int()
  }),
  reconnectionIntervalMs: z.number().int().optional(),
  meeting_id: z.number().int().optional(),
  botManagerCallbackUrl: z.string().url().optional(),
  credentials: z.object({
    googleUsername: z.string().optional(),
    googlePassword: z.string().optional(),
    mfaSecret: z.string().optional()
  }).optional()
});


(function main() {
const rawConfig = process.env.BOT_CONFIG;
if (!rawConfig) {
  console.error("BOT_CONFIG environment variable is not set");
  process.exit(1);
}

  try {
  // Parse the JSON string from the environment variable
  const parsedConfig = JSON.parse(rawConfig);
  // Validate and parse the config using zod
  const botConfig: BotConfig = BotConfigSchema.parse(parsedConfig) as BotConfig;

  // Run the bot with the validated configuration
  runBot(botConfig).catch((error) => {
    console.error("Error running bot:", error);
    process.exit(1);
  });
} catch (error) {
  console.error("Invalid BOT_CONFIG:", error);
  process.exit(1);
}
})()
