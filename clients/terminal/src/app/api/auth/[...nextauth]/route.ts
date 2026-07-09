/** NextAuth catch-all handler. Config lives in ./authOptions (an App Router route.ts may only export
 *  HTTP handlers). See authOptions.ts for why NextAuth is only the OAuth broker here. */
import NextAuth from "next-auth";
import { authOptions } from "./authOptions";

const handler = NextAuth(authOptions);

export { handler as GET, handler as POST };
