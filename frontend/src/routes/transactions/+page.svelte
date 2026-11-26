<script lang="ts">
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import Download from '@lucide/svelte/icons/download';
	import Filter from '@lucide/svelte/icons/filter';
	import { goto, invalidate } from '$app/navigation';
	import { page } from '$app/state';
	import { get, post } from '#lib/api.js';
	import { todayIso } from '#lib/format/date.js';
	import { money } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import CategoryDialog from '#lib/transactions/CategoryDialog.svelte';
	import Context from '#lib/transactions/Context.svelte';
	import Days from '#lib/transactions/Days.svelte';
	import Filters from '#lib/transactions/Filters.svelte';
	import Summary from '#lib/transactions/Summary.svelte';
	import {
		formatDetail,
		parseDetail,
		type DetailKind,
		type DetailTarget
	} from '#lib/transactions/detail.js';
	import { FIELDS, filtered } from '#lib/transactions/filter.js';
	import type {
		KnownMerchants,
		Transaction,
		Transactions,
		TransactionsContext
	} from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import EmptyState from '#lib/ui/EmptyState.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import SidePanel from '#lib/ui/SidePanel.svelte';
	import { detailOf, here, oneOf, pick, setDetail, show, withQuery } from '#lib/view.js';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const listed = $derived(data.listed);
	const context = $derived(data.context);
	const session = $derived(data.session);
	const owner = $derived(page.url.searchParams.get('owner'));
	const headerCurrency = $derived(here().searchParams.get('currency') ?? session.currency);
	const people = $derived(session.people.length > 1 && !owner);
	const values = $derived(
		Object.fromEntries(FIELDS.map((name) => [name, page.url.searchParams.get(name) ?? '']))
	);
	const cut = $derived(filtered(page.url));
	const today = todayIso();

	const view = $derived(oneOf(here(), 'view', ['list', 'categories'], 'list'));
	const viewOptions = $derived([
		{ value: 'list' as const, label: t('transactions.viewList') },
		{ value: 'categories' as const, label: t('transactions.viewCategories') }
	]);

	function change(changes: Record<string, string | null>, typing = false) {
		return goto(withQuery(here(), changes), { reset: false, replace: typing });
	}

	// The rows after the first page, fetched as they are asked for; key on the filter params only
	// so toggling List / By category or opening the side panel never resets loaded pagination.
	const PAGE = 100; // as the API sends at once
	const MOST = 500; // the most it sends at once
	const filterKey = $derived(pick(page.url, ['owner', ...FIELDS]).toString());
	let more = $state<Transaction[]>([]);
	let fetching = $state(false);
	let shownFor = '';
	$effect.pre(() => {
		if (filterKey !== shownFor) {
			shownFor = filterKey;
			more = [];
		}
	});
	const rows = $derived([...listed.rows, ...more]);

	let unloaded = $state<string | null>(null);

	/** Fetch `limit` rows after the first page and those already shown; an answer for a filter
	 * the reader has since left is dropped. */
	async function fetchMore(limit: number) {
		const asked = filterKey;
		const params = pick(page.url, ['owner', ...FIELDS]);
		params.set('offset', String(listed.rows.length + more.length));
		params.set('limit', String(limit));
		fetching = true;
		unloaded = null;
		try {
			const next = await get<Transactions>(fetch, '/api/transactions', params);
			if (filterKey === asked) more = [...more, ...next.rows];
		} catch {
			unloaded = t('transactions.loadMoreError');
		} finally {
			fetching = false;
		}
	}
	const showMore = () => fetchMore(PAGE);

	// Lifted CategoryDialog state: a single modal dialog for editing any row's category.
	let editingRow = $state<Transaction | null>(null);
	let lastEditTrigger = $state<HTMLElement | null>(null);

	function openEdit(row: Transaction, trigger: HTMLElement) {
		lastEditTrigger = trigger;
		editingRow = row;
	}

	function closeEdit() {
		editingRow = null;
	}

	// SidePanel state for category and merchant details, backed by URL query param `detail`.
	const rawDetail = $derived(detailOf(here(), 'detail'));
	const detail = $derived(parseDetail(rawDetail));

	let detailContext = $state<TransactionsContext | null>(null);
	let loadedDetailKey = $state<string | null>(null);
	let detailLoading = $state(false);
	let detailError = $state<string | null>(null);
	let displayedDetail = $state<DetailTarget | null>(null);
	let lastDetailTrigger = $state<HTMLElement | null>(null);
	let detailRevision = $state(0);
	let detailSeq = 0;

	$effect(() => {
		if (!detail) {
			detailSeq += 1;
			detailLoading = false;
			return;
		}
		const target = detail;
		const key = rawDetail;
		const currentOwner = owner;
		const currentCurrency = headerCurrency;
		// Re-fetch open panel context when language or data revision changes.
		void session.language;
		void detailRevision;
		displayedDetail = target;
		detailError = null;
		detailLoading = true;
		const seq = ++detailSeq;
		const params = new URLSearchParams();
		if (currentOwner) params.set('owner', currentOwner);
		params.set('currency', currentCurrency);
		params.set(target.kind, target.name);
		get<TransactionsContext>(fetch, '/api/transactions/context', params)
			.then((sent) => {
				if (seq !== detailSeq) return;
				detailContext = sent;
				loadedDetailKey = key;
				detailLoading = false;
			})
			.catch((error) => {
				if (seq !== detailSeq) return;
				detailContext = null;
				loadedDetailKey = null;
				detailError = error instanceof Error ? error.message : t('transactions.detailLoadError');
				detailLoading = false;
			});
	});

	const panelDetail = $derived(detail ?? displayedDetail);
	const panelContextMatches = $derived(
		Boolean(
			detailContext &&
			panelDetail &&
			detailContext.kind === panelDetail.kind &&
			(detail === null || loadedDetailKey === rawDetail)
		)
	);
	const panelContext = $derived(panelContextMatches ? detailContext : null);
	const panelTitle = $derived(panelContext?.name ?? panelDetail?.name ?? '');
	const panelDescription = $derived.by(() => {
		if (!panelDetail) return undefined;
		const kindLabel =
			panelDetail.kind === 'category'
				? t('transactions.kindCategory')
				: t('transactions.kindMerchant');
		return `${kindLabel}, ${t('transactions.lastTwoYears')}`;
	});

	function openDetail(kind: DetailKind, name: string, trigger?: HTMLElement | null) {
		if (trigger) lastDetailTrigger = trigger;
		const encoded = formatDetail(kind, name);
		void setDetail('detail', rawDetail === encoded ? null : encoded);
	}

	function closeDetail() {
		void setDetail('detail', null);
	}

	const filterHref = $derived.by(() => {
		if (!panelDetail || !panelTitle) return null;
		return withQuery(
			here(),
			panelDetail.kind === 'category'
				? { category: panelTitle, merchant: null, account: null, view: null, detail: null }
				: { merchant: panelTitle, category: null, account: null, view: null, detail: null }
		);
	});

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
		detailRevision += 1;
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
	<EmptyState
		heading={t('transactions.emptyHeading')}
		body={t('transactions.emptyBody')}
		importCta={!session.read_only}
	/>
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

	<div class="mt-5 flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
		<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
			{#if listed.count}
				<Segmented
					label={t('transactions.viewAria')}
					value={view}
					options={viewOptions}
					onchange={(next) =>
						show(here(), { view: next === 'list' ? null : next }, { shallow: true })}
				/>
			{/if}
			<p class="text-body text-ink-secondary" aria-live="polite">
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
		</div>
		<span class="flex items-center gap-4">
			{#if cut}
				<a
					href={withQuery(here(), Object.fromEntries(FIELDS.map((name) => [name, null])))}
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
	{:else if view === 'categories'}
		<div class="mt-4">
			<Summary
				totals={listed.by_category}
				count={listed.count}
				{headerCurrency}
				selected={detail?.kind === 'category' ? (panelContext?.name ?? detail.name) : null}
				onselect={(line, trigger) => openDetail('category', line.name, trigger)}
			/>
		</div>
	{:else}
		<div class="mt-4">
			<Days
				{rows}
				booking={Boolean(values.booking)}
				{people}
				changeable={!session.read_only}
				thisYear={Number(today.slice(0, 4))}
				activeDetail={rawDetail}
				ondetail={openDetail}
				onedit={openEdit}
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
	{/if}

	<CategoryDialog
		row={session.read_only ? null : editingRow}
		leaves={listed.choices.leaves}
		rules={session.rules}
		returnFocus={() => lastEditTrigger}
		onclose={closeEdit}
		onsave={recategorize}
	/>

	<SidePanel
		open={Boolean(detail)}
		title={panelTitle}
		description={panelDescription}
		subject={rawDetail}
		onclose={closeDetail}
		returnFocus={() => lastDetailTrigger}
	>
		{#if filterHref && panelDetail}
			<div class="mb-4">
				<a
					href={filterHref}
					class="inline-flex h-8 items-center gap-1.5 rounded-control border border-line bg-raised px-3 text-small text-ink hover:border-line-strong"
				>
					<Filter class="size-3.5 text-ink-secondary" aria-hidden="true" />
					{t(
						panelDetail.kind === 'category'
							? 'transactions.filterByCategory'
							: 'transactions.filterByMerchant'
					)}
				</a>
			</div>
		{/if}

		{#if detailLoading && !panelContext}
			<p class="text-small text-ink-secondary">{t('transactions.detailLoading')}</p>
		{:else if detailError}
			<p class="flex items-start gap-2 text-small text-loss" role="alert">
				<CircleAlert class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
				{detailError}
			</p>
		{:else if panelContext}
			<div class={detailLoading ? 'opacity-60 transition-opacity' : undefined}>
				<Context
					context={panelContext}
					embedded
					onmerchant={(name) => openDetail('merchant', name)}
				/>
			</div>
		{/if}
	</SidePanel>
{/if}
