import { onward } from '#lib/renamed.js';
import type { PageLoad } from './$types';

// Statements is called Accounts now: an old link to it (to its checks too) still finds the page.
export const load: PageLoad = onward('/statements');
