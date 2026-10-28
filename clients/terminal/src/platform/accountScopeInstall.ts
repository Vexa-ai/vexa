/** Imported FIRST by the composition root (app/App.tsx): every module that reads browser storage at
 *  load then reads it through the account scope (see accountStorage.ts). On the server it does nothing. */
import { installAccountScope } from "./accountStorage";

if (typeof window !== "undefined") installAccountScope();
