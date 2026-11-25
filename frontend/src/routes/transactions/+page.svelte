<script lang="ts">
	import { resolve } from '$app/paths';
	import Download from '@lucide/svelte/icons/download';
	import { goto, invalidate } from '$app/navigation';
	import { page } from '$app/state';
	import { get, post } from '#lib/api.js';
	import { money } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import Context from '#lib/transactions/Context.svelte';
	import Days from '#lib/transactions/Days.svelte';
	import Filters from '#lib/transactions/Filters.svelte';
	import Summary from '#lib/transactions/Summary.svelte';
	import { FIELDS, TRANSFERS, filtered } from '#lib/transactions/filter.js';
	import type { KnownMerchants, Transaction, Transactions } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import { pick, withQuery } from '#lib/view.js';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const listed = $derived(data.listed);
	const context = $derived(data.context);
	const session = $derived(data.session);
	const owner = $derived(page.url.searchParams.get('owner'));
	const people = $derived(session.people.length > 1 && !owner);
	const values = $derived(
		Object.fromEntries(FIELDS.map((name) => [name, page.url.searchParams.get(name) ?? '']))
	);
	const cut = $derived(filtered(page.url));

	const now = new Date();
	const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

	function change(changes: Record<string, string | null>, typing = false) {
		return goto(withQuery(page.url, changes), { reset: false, replace: typing });
	}

	// The rows after the first page, fetched as they are asked for; a filter of its own starts
	// again from the first page.
	const PAGE = 100; // as the API sends at once
	const MOST = 500; // the most it sends at once
	let more = $state<Transaction[]>([]);
	let fetching = $state(false);
	let shownFor = '';
	$effect.pre(() => {
		const search = page.url.search;
		if (search !== shownFor) {
			shownFor = search;
			more = [];
		}
	});
	const rows = $derived([...listed.rows, ...more]);

	let unloaded = $state<string | null>(null);

	/** Fetch `limit` rows after the first page and those already shown; an answer for a filter
	 * the reader has since left is dropped. */
	async function fetchMore(limit: number) {
		const asked = page.url.search;
		const params = pick(page.url, ['owner', ...FIELDS]);
		params.set('offset', String(listed.rows.length + more.length));
		params.set('limit', String(limit));
		fetching = true;
		unloaded = null;
		try {
			const next = await get<Transactions>(fetch, '/api/transactions', params);
			if (page.url.search === asked) more = [...more, ...next.rows];
		} catch {
			unloaded = t('transactions.loadMoreError');
		} finally {
			fetching = false;
		}
	}
	const showMore = () => fetchMore(PAGE);

	async function recategorize(row: Transaction, category: string, remember: boolean) {
		await post(
			'/api/transactions/category',
			{ owner: row.owner, ids: [row.id], category, remember },
			session.token
		);
		// A row may now fall out of the filter, which moves every page after it: the rows shown
		// after the first page are fetched again, as many as there were.
		const shown = more.length;
		await invalidate('api:transactions');
		more = [];
		if (shown) await fetchMore(Math.min(shown, MOST));
	}

	// The merchants the Merchant chip offers: asked for once per owner and currency, and again
	// only when that failed.
	const merchantLists = new Map<string, Promise<KnownMerchants>>();
	function merchantsShown(): Promise<KnownMerchants> {
		const params = pick(page.url, ['owner', 'currency']);
		const key = params.toString();
		let list = merchantLists.get(key);
		if (!list) {
			list = get<KnownMerchants>(fetch, '/api/transactions/merchants', params);
			list.catch(() => merchantLists.delete(key));
			merchantLists.set(key, list);
		}
		return list;
	}

	const csv = $derived.by(() => {
		const query = pick(page.url, ['owner', ...FIELDS]).toString();
		return `/transactions.csv${query ? `?${query}` : ''}`;
	});
</script>

<svelte:head>
	<title>{t('transactions.pageTitle')}</title>
</svelte:head>

{#if !listed.count && !cut}
	<section class="rounded-lead border border-line bg-panel px-6 py-16 text-center">
		<h1 class="text-heading font-medium text-ink">{t('transactions.emptyHeading')}</h1>
		<p class="mx-auto mt-2 max-w-prose text-body text-ink-secondary">
			{t('transactions.emptyBody')}
		</p>
		{#if !session.read_only}
			<a
				href={resolve('/import')}
				class="mt-6 inline-flex rounded-control bg-brand px-4 py-2 text-body font-medium text-on-brand hover:bg-brand-hover"
			>
				{t('overview.emptyCta')}
			</a>
		{/if}
	</section>
{:else}
	<h1 class="sr-only">{t('transactions.heading')}</h1>
	<Filters
		{values}
		accounts={listed.choices.accounts}
		categories={listed.choices.categories}
		kinds={listed.kinds}
		{today}
		merchants={merchantsShown}
		onchange={change}
	/>

	{#if context}
		<div class="mt-5">
			<Context
				{context}
				merchant={(name) =>
					withQuery(page.url, {
						merchant: name,
						category: null,
						account: null,
						currency: context.currency
					})}
			/>
		</div>
	{/if}

	<div
		class="mt-5 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2"
		aria-live="polite"
	>
		<p class="text-body text-ink-secondary">
			<span class="text-title font-medium text-ink">
				{listed.count === 1
					? t('transactions.countOne')
					: t('transactions.countMany', { count: money(String(listed.count), 0) })}
			</span>
			{#if listed.totals.length}
				<span class="ml-1">{t('transactions.addUpTo')}</span>
				{#each listed.totals as total, index (total.currency)}
					{#if index === 0}{' '}{:else if index === listed.totals.length - 1}{' '}
						{t('common.andWord')}{' '}{:else},
					{/if}<Amount value={total.total} currency={total.currency} class="text-ink" />
				{/each}
			{/if}
		</p>
		<span class="flex items-center gap-4">
			{#if cut}
				<a
					href={withQuery(page.url, Object.fromEntries(FIELDS.map((name) => [name, null])))}
					class="text-small text-brand hover:text-brand-hover"
				>
					{t('transactions.clearFilters')}
				</a>
			{/if}
			{#if listed.count}
				<a
					href={csv}
					data-sveltekit-reload
					class="inline-flex items-center gap-1.5 text-small text-ink-secondary hover:text-ink"
				>
					<Download class="size-4" aria-hidden="true" />
					{t('transactions.downloadCsv')}
				</a>
			{/if}
		</span>
	</div>

	{#if !listed.count}
		<section class="mt-6 rounded-panel bg-panel px-6 py-12 text-center">
			<p class="text-title text-ink">{t('transactions.noMatchHeading')}</p>
			<p class="mt-1 text-body text-ink-secondary">{t('transactions.noMatchBody')}</p>
		</section>
	{:else}
		<div class="mt-5 grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
			<div class="lg:order-last lg:sticky lg:top-20">
				<Summary
					totals={listed.by_category}
					chosen={values.category || null}
					onchoose={(line) =>
						change(
							(line.key ?? line.name) === TRANSFERS
								? { kind: 'internal' }
								: { category: values.category === line.name ? null : line.name }
						)}
				/>
			</div>

			<div>
				<Days
					{rows}
					booking={Boolean(values.booking)}
					leaves={listed.choices.leaves}
					{people}
					rules={session.rules}
					changeable={!session.read_only}
					thisYear={now.getFullYear()}
					onchange={recategorize}
				/>
				{#if rows.length < listed.count}
					<div class="mt-4 flex items-center justify-center gap-3 text-small text-ink-secondary">
						<span
							>{t('transactions.pageOf', {
								shown: money(String(rows.length), 0),
								total: money(String(listed.count), 0)
							})}</span
						>
						<button
							type="button"
							onclick={showMore}
							disabled={fetching}
							class="h-8 rounded-control border border-line bg-panel px-3 text-ink hover:border-line-strong disabled:opacity-60"
						>
							{t(fetching ? 'transactions.loadingMore' : 'transactions.showMore')}
						</button>
					</div>
					{#if unloaded}
						<p class="mt-2 text-center text-small text-loss" role="alert">{unloaded}</p>
					{/if}
				{/if}
			</div>
		</div>
	{/if}
{/if}
