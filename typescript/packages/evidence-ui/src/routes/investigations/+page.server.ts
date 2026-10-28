import { evidence } from "$lib/server/source.ts";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async () => ({ sets: await evidence().investigationSets() });
