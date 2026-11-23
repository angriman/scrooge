import { get } from "#lib/api.js";
import { FIELDS } from "#lib/transactions/filter.js";
import type { Transactions } from "#lib/types.js";
import { pick } from "#lib/view.js";
import type { PageLoad } from "./$types";

export const load: PageLoad = async ({ fetch, url, depends }) => {
    depends("api:transactions");
    return {
        listed: await get<Transactions>(
            fetch,
            "/api/transactions",
            pick(url, ["owner", ...FIELDS]),
        ),
    };
};
