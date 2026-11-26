<!--
@component
Details of one held instrument inside the side panel: its current value and gain, a small value
chart over time, its positions account by account in the instrument's currency, what it returned
over each range, its latest committed transactions, and its logo.
-->
<script lang="ts">
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import FileText from '@lucide/svelte/icons/file-text';
	import { resolve } from '$app/paths';
	import { assetColor } from '#lib/design/data.js';
	import { day } from '#lib/format/date.js';
	import { exact, money, percent } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import { kindName } from '#lib/transactions/filter.js';
	import type {
		HeldInstrument,
		HoldingDetail,
		HoldingDetailResponse,
		Position,
		Transaction
	} from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Change from '#lib/ui/Change.svelte';
	import Share from '#lib/ui/Share.svelte';
	import { cn } from '#lib/ui/cn.js';
	import HoldingReturns from './HoldingReturns.svelte';
	import LogoControl from './LogoControl.svelte';
	import {
		chartGeometry,
		detailParams,
		holdingTransactionsParams,
		nearestPoint
	} from './detail.js';
	import { holdingName } from './names.js';

	interface Props {
		holding: HeldInstrument;
		/** Whether this instrument currently has a custom logo. */
		hasLogo: boolean;
		currency: string;
		/** The last day: a price from another day says so. */
		lastDay: string;
		/** Whether several people's positions are listed: each says whose it is. */
		people: boolean;
		/** Owner filter currently active on the page, or null for the household. */
		owner?: string | null;
		/** The token a change of a logo needs; null where logos cannot be changed. */
		token?: string | null;
		/** Called once a logo is changed. */
		onlogo?: () => void | Promise<void>;
	}

	let {
		holding,
		hasLogo,
		currency,
		lastDay,
		people,
		owner = null,
		token = null,
		onlogo = () => {}
	}: Props = $props();

	const CHART_W = 420;
	const CHART_H = 120;

	const color = $derived(assetColor(holding.asset));
	const displayName = $derived(holdingName(holding));

	let detail = $state<HoldingDetail | null>(null);
	let detailErrors = $state<string[]>([]);
	let loading = $state(false);
	let loadError = $state<string | null>(null);
	let hovered = $state<number | null>(null);

	$effect(() => {
		const params = detailParams(holding, currency, owner);
		const controller = new AbortController();
		loading = true;
		loadError = null;
		detail = null;
		detailErrors = [];
		hovered = null;

		void (async () => {
			try {
				const response = await fetch(`/api/holdings/detail?${params}`, {
					signal: controller.signal
				});
				if (controller.signal.aborted) return;
				if (response.status === 401) {
					location.assign(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
					return;
				}
				const sent = (await response.json().catch(() => null)) as
					(Partial<HoldingDetailResponse> & { errors?: string[] }) | null;
				if (controller.signal.aborted) return;
				if (!response.ok || !sent?.detail) {
					loadError = (sent?.errors ?? []).join(' ') || t('holdings.chartUnavailable');
				} else {
					detail = sent.detail;
					detailErrors = sent.errors ?? [];
				}
			} catch (err) {
				if (controller.signal.aborted) return;
				if (!(err instanceof Error && err.name === 'AbortError')) {
					loadError = t('holdings.chartUnavailable');
				}
			} finally {
				if (!controller.signal.aborted) loading = false;
			}
		})();

		return () => controller.abort();
	});

	const geometry = $derived(
		detail?.series.length ? chartGeometry(detail.series, CHART_W, CHART_H) : null
	);
	const activePoint = $derived(
		geometry ? geometry.points[hovered ?? geometry.points.length - 1] : null
	);
	const transactionsHref = $derived(
		`${resolve('/transactions')}?${holdingTransactionsParams(holding, owner)}`
	);

	function handlePointerMove(event: PointerEvent) {
		if (!geometry) return;
		const svg = event.currentTarget as SVGSVGElement;
		const rect = svg.getBoundingClientRect();
		if (!rect.width) return;
		const x = ((event.clientX - rect.left) / rect.width) * CHART_W;
		hovered = nearestPoint(geometry.points, x);
	}

	const priced = (position: Position) => {
		if (position.price === null) return '–';
		return exact(position.price);
	};
	const priceNote = (position: Position) =>
		position.price_is_trade
			? t('holdings.lastTradePrice')
			: position.price_day && position.price_day !== lastDay
				? t('holdings.closeOf', { date: day(position.price_day) })
				: '';

	const amountTone = (row: Transaction) =>
		row.kind === 'internal' || row.kind === 'transfer'
			? 'text-ink-secondary'
			: row.amount.startsWith('-')
				? 'text-ink'
				: 'text-gain';
</script>

<div
	class="mb-5 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-b border-line pb-4"
>
	<div>
		<p class="text-figure font-medium tracking-tight text-ink">
			<Amount value={holding.value} {currency} places={0} />
		</p>
		<p class="mt-1 flex items-center gap-2 text-small text-ink-secondary tabular-nums">
			<Share share={holding.share} {color} class="w-16" />
			<span>{percent(holding.share)}</span>
		</p>
	</div>
	<div class="text-left sm:text-right">
		{#if holding.gain === null}
			<p class="text-small text-ink-muted">{t('holdings.costNotKnown')}</p>
		{:else}
			<p class="text-caption text-ink-muted">{t('investments.sinceBought')}</p>
			<Change amount={holding.gain} percent={holding.return_percent} class="mt-0.5 text-body" />
		{/if}
	</div>
</div>

<!-- Value over time chart -->
<section class="mb-5 border-b border-line pb-4">
	<div class="mb-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
		<p class="text-caption text-ink-muted">{t('holdings.chartTitle', { currency })}</p>
		{#if activePoint}
			<p class="text-small text-ink-secondary tabular-nums" aria-live="polite">
				<span>{day(activePoint.day)}</span>
				<span class="mx-1 text-ink-muted">·</span>
				<Amount value={activePoint.value} {currency} places={0} class="font-medium text-ink" />
			</p>
		{/if}
	</div>

	{#if loading}
		<p class="py-6 text-small text-ink-muted" aria-busy="true">{t('holdings.chartLoading')}</p>
	{:else if loadError}
		<p class="flex items-start gap-2 py-2 text-small text-ink-secondary" role="status">
			<CircleAlert class="mt-0.5 size-3.5 shrink-0 text-caution" aria-hidden="true" />
			{loadError}
		</p>
	{:else if detailErrors.length}
		{#each detailErrors as err (err)}
			<p class="flex items-start gap-2 py-2 text-small text-ink-secondary" role="status">
				<CircleAlert class="mt-0.5 size-3.5 shrink-0 text-caution" aria-hidden="true" />
				{err}
			</p>
		{/each}
	{:else if geometry}
		<svg
			viewBox="0 0 {CHART_W} {CHART_H}"
			class="block h-28 w-full touch-pan-y select-none"
			role="img"
			aria-label={t('holdings.chartAria', { name: displayName, currency })}
			onpointermove={handlePointerMove}
			onpointerleave={() => (hovered = null)}
		>
			<line
				x1="6"
				x2={CHART_W - 6}
				y1={geometry.baselineY}
				y2={geometry.baselineY}
				class="stroke-line"
				stroke-width="1"
			/>
			{#if geometry.points.length === 1}
				<line
					x1="6"
					x2={CHART_W - 6}
					y1={geometry.points[0].y}
					y2={geometry.points[0].y}
					stroke={color}
					stroke-width="1.5"
					stroke-dasharray="3 3"
					opacity="0.5"
				/>
				<circle cx={geometry.points[0].x} cy={geometry.points[0].y} r="4" fill={color} />
			{:else}
				<path d={geometry.areaPath} style:fill="color-mix(in srgb, {color} 18%, transparent)" />
				<path
					d={geometry.linePath}
					fill="none"
					stroke={color}
					stroke-width="2"
					stroke-linejoin="round"
					stroke-linecap="round"
				/>
				{#if activePoint && hovered !== null}
					<line
						x1={activePoint.x}
						x2={activePoint.x}
						y1="6"
						y2={geometry.baselineY}
						class="stroke-line-strong"
						stroke-width="1"
						stroke-dasharray="2 2"
					/>
					<circle cx={activePoint.x} cy={activePoint.y} r="3.5" fill={color} />
				{/if}
			{/if}
		</svg>
		<div class="mt-1 flex justify-between text-caption text-ink-muted tabular-nums">
			<span>{day(geometry.points[0].day)}</span>
			{#if geometry.points.length > 1}
				<span>{day(geometry.points[geometry.points.length - 1].day)}</span>
			{/if}
		</div>
	{:else}
		<p class="py-3 text-small text-ink-muted">{t('holdings.chartNoData')}</p>
	{/if}
</section>

<div class="overflow-x-auto">
	<table class="w-full text-small">
		<thead class="text-caption text-ink-muted">
			<tr>
				<th class="pb-2 text-left font-normal">{t('investments.colAccount')}</th>
				<th class="pb-2 pl-3 text-right font-normal">{t('holdings.colQuantity')}</th>
				<th class="pb-2 pl-3 text-right font-normal">{t('holdings.colPrice')}</th>
				<th class="pb-2 pl-3 text-right font-normal">{t('holdings.colValue')}</th>
				<th class="pb-2 pl-3 text-right font-normal">{t('holdings.colCost')}</th>
				<th class="pb-2 pl-3 text-right font-normal">{t('investments.sinceBought')}</th>
			</tr>
		</thead>
		<tbody>
			{#each holding.positions as position (position.owner + position.account)}
				<tr class="border-t border-line">
					<td class="py-2 whitespace-nowrap">
						{position.account}{#if people}<span class="text-ink-muted">, {position.owner}</span
							>{/if}
					</td>
					<td class="py-2 pl-3 text-right tabular-nums">{exact(position.quantity)}</td>
					<td class="py-2 pl-3 text-right whitespace-nowrap tabular-nums">
						{priced(position)}
						{#if priceNote(position)}<span class="block text-caption text-ink-muted"
								>{priceNote(position)}</span
							>{/if}
					</td>
					<td class="py-2 pl-3 text-right whitespace-nowrap tabular-nums">
						{money(position.value_native)}
						<span class="text-caption text-ink-muted">{holding.currency}</span>
					</td>
					<td class="py-2 pl-3 text-right whitespace-nowrap tabular-nums">
						{position.cost_native === null ? t('common.notKnown') : money(position.cost_native)}
					</td>
					<td class="py-2 pl-3 text-right whitespace-nowrap">
						<Change amount={position.gain_native} percent={position.gain_percent} />
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<HoldingReturns {holding} {currency} {people} />

<!-- Latest committed transactions -->
{#if detail}
	<section class="mt-5 border-t border-line pt-4">
		<div class="mb-2 flex flex-wrap items-baseline justify-between gap-2">
			<h3 class="text-caption text-ink-muted">{t('holdings.transactionsTitle')}</h3>
			{#if detail.total_transactions > 0}
				<a
					href={transactionsHref}
					class="text-caption font-medium text-brand hover:text-brand-hover"
				>
					{detail.total_transactions > detail.transactions.length
						? t('holdings.transactionsAllCount', { count: detail.total_transactions })
						: t('holdings.transactionsAll')}
				</a>
			{/if}
		</div>
		{#if !detail.transactions.length}
			<p class="text-small text-ink-muted">{t('holdings.transactionsEmpty')}</p>
		{:else}
			<ul class="divide-y divide-line">
				{#each detail.transactions as row (row.id)}
					<li class="flex items-start justify-between gap-3 py-2 text-small">
						<div class="min-w-0">
							<p class="truncate text-ink">{row.counterparty || row.description}</p>
							<p class="truncate text-caption text-ink-muted">
								{day(row.date)} · {row.category ?? kindName(row.kind)} · {row.account}{#if people},
									{row.owner}{/if}
							</p>
						</div>
						<div class="flex shrink-0 items-center gap-1.5 text-right">
							<span class={cn('whitespace-nowrap', amountTone(row))}>
								<Amount value={row.amount} currency={row.currency} />
							</span>
							{#if row.original}
								<a
									href={row.original}
									data-sveltekit-reload
									class="inline-flex text-ink-muted hover:text-brand"
									title={t('transactions.statementFileTitle')}
									aria-label={t('transactions.statementFileAria', { account: row.account })}
								>
									<FileText class="size-3.5" aria-hidden="true" />
								</a>
							{/if}
						</div>
					</li>
				{/each}
			</ul>
		{/if}
	</section>
{/if}

{#if token && holding.asset !== 'Bills'}
	<LogoControl symbol={holding.symbol} has={hasLogo} {token} onchange={onlogo} />
{/if}
