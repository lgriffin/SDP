import { demonstration } from "$lib/server/source.ts";

import type { LayoutServerLoad } from "./$types";

export const load: LayoutServerLoad = async () => ({ demonstration: demonstration() });
