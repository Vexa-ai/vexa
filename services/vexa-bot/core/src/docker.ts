import { runBot } from "."
import { z } from 'zod';
import { BotConfig } from "./types"; // Import the BotConfig type

// Define a schema that matches your JSON configuration
export const BotConfigSchema = z.object({
  platform: z.enum(["google_meet", "zoom", "teams"]),
  meetingUrl: z.string().url().nullable(), // Allow null from BOT_CONFIG
  botName: z.string(),
  token: z.string(),
  connectionId: z.string(),
  nativeMeetingId: z.string(), // *** ADDED schema field ***
  language: z.string().nullish(), // Optional language
  task: z.string().nullish(),     // Optional task
  redisUrl: z.string(),         // Required Redis URL
  container_name: z.string().optional(), // ADDED: Optional container name
  automaticLeave: z.object({
    waitingRoomTimeout: z.number().int(),
    noOneJoinedTimeout: z.number().int(),
    everyoneLeftTimeout: z.number().int()
  }),
  reconnectionIntervalMs: z.number().int().optional(), // ADDED: Optional reconnection interval
  meeting_id: z.number().int().optional(), // Allow optional internal ID
  botManagerCallbackUrl: z.string().url().optional() // ADDED: Optional callback URL
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
