<!--
@component
How the investments did, in short: the time-weighted return over the range the net worth chart
is looked at over, with what they gained; and what they paid in the last twelve months, after
the tax withheld. Each links to the investments page, which tells it in full. Either can be
missing, when the app could not answer for it: the other stays, with a sentence saying why.
-->
<script lang="ts">
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { signed, signedPercent } from '#lib/format/number.js';
	import { direction } from '#lib/investments/performance.js';
	import type { InvestmentIncome, InvestmentPerformance } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { returnSpan, taxWithheld, type Loaded } from './investments.js';
	import type { Range } from './range.js';

	interface Props {
		/** What they returned over `range`: still on its way when the range was just chosen. */
		returned: Loaded<InvestmentPerformance> | Promise<Loaded<InvestmentPerformance>>;
		paid: Loaded<InvestmentIncome>;
		range: Range;
		/** Where the returns and the income are told in full. */
		returnsHref: string;
		incomeHref: string;
	}

	let { returned, paid, range, returnsHref, incomeHref }: Props = $props();

	const income = $derived(paid.sent?.income ?? null);
	const tax = $derived(income ? taxWithheld(income.trailing) : null);

	const tone = (value: string | null, places: number) =>
		({ up: 'text-gain', down: 'text-loss', flat: 'text-ink' })[direction(value, places)];
</script>

{#snippet more(href: string, label: string)}
	<a
		{href}
		class="inline-flex items-center gap-0.5 text-small font-medium whitespace-nowrap text-brand hover:text-brand-hover"
	>
		{label}<ChevronRight class="size-3.5" aria-hidden="true" />
	</a>
{/snippet}

{#snippet problems(errors: string[])}
	{#each new Set(errors) as error (error)}
		<p class="mt-2 flex items-start gap-1.5 text-small text-ink-secondary" role="status">
			<CircleAlert class="mt-0.5 size-3.5 shrink-0 text-caution" aria-hidden="true" />
			{error}
		</p>
	{/each}
{/snippet}

<Panel title="Investments">
	<div class="grid gap-4">
		<section aria-label="What the investments returned">
			{#await returned}
				<div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
					<p class="text-small text-ink-secondary">Time-weighted return</p>
					{@render more(returnsHref, 'Performance')}
				</div>
				<p class="mt-1 text-figure text-ink-muted" aria-busy="true">…</p>
			{:then shown}
				{@const report = shown.sent?.performance ?? null}
				{@const totals = report?.totals ?? null}
				{@const span = report ? returnSpan(range, report) : ''}
				<div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
					<p class="text-small text-ink-secondary">
						Time-weighted return{span ? `, ${span}` : ''}
					</p>
					{@render more(returnsHref, 'Performance')}
				</div>
				{#if report && !totals}
					<p class="mt-1 text-body text-ink-muted">No investment account yet.</p>
				{:else if totals}
					{#if totals.twr === null}
						<p class="mt-1 text-body text-ink-muted">Not known: nothing was invested.</p>
					{:else}
						<p class={cn('mt-1 text-figure font-medium tabular-nums', tone(totals.twr, 1))}>
							{signedPercent(totals.twr)}
						</p>
					{/if}
					<p class="mt-1 text-small text-ink-secondary">
						Gain
						<span class={cn('font-medium tabular-nums', tone(totals.gain, 0))}>{signed(totals.gain, 0)}</span>
						<span class="text-ink-muted">{report!.currency}</span>
					</p>
				{/if}
				{@render problems(shown.errors)}
			{/await}
		</section>

		<section class="border-t border-line pt-4" aria-label="What the investments paid">
			<div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
				<p class="text-small text-ink-secondary">Paid in the last 12 months</p>
				{@render more(incomeHref, 'Income')}
			</div>
			{#if income}
				<p class="mt-1 text-figure font-medium text-ink">
					<Amount value={income.trailing.net} currency={income.currency} places={0} />
				</p>
				<p class="mt-1 text-small text-ink-secondary">
					Dividends and interest{#if tax}, after <Amount value={tax} currency={income.currency} places={0} /> tax withheld{/if}
				</p>
			{/if}
			{@render problems(paid.errors)}
		</section>
	</div>
</Panel>
