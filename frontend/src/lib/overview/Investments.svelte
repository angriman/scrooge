<!--
@component
How the investments did, in short: the time-weighted return over the range the net worth chart
is looked at over, with what they gained; and what they paid in the last twelve months, after
the tax withheld. Each links to the investments page, which tells it in full. Each comes on its
own, after the rest of the page, and either can be missing when the app could not answer for
it: the other stays, with a sentence saying why.
-->
<script lang="ts">
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { signed, signedPercent } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import { direction } from '#lib/investments/performance.js';
	import type { InvestmentIncome, InvestmentPerformance } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import { cn } from '#lib/ui/cn.js';
	import {
		incomeShown,
		paidLabel,
		refusal,
		returnedLabel,
		returnShown,
		type Loaded
	} from './investments.js';
	import type { Range } from './range.js';

	type Maybe<T> = Loaded<T> | Promise<Loaded<T>>;

	interface Props {
		/** What they returned over `range`, and what they paid: each still on its way at first. */
		returned: Maybe<InvestmentPerformance>;
		paid: Maybe<InvestmentIncome>;
		range: Range;
		/** Where the returns and the income are told in full. */
		returnsHref: string;
		incomeHref: string;
	}

	let { returned, paid, range, returnsHref, incomeHref }: Props = $props();

	const tone = (value: string | null, places: number) =>
		({ up: 'text-gain', down: 'text-loss', flat: 'text-ink' })[direction(value, places)];
</script>

{#snippet heading(label: string, href: string, link: string)}
	<div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
		<p class="text-small text-ink-secondary">{label}</p>
		<a
			{href}
			class={cn(
				'-my-3 inline-flex min-h-11 items-center gap-0.5 whitespace-nowrap sm:my-0 sm:min-h-0',
				'text-small font-medium text-brand hover:text-brand-hover'
			)}
		>
			{link}<ChevronRight class="size-3.5" aria-hidden="true" />
		</a>
	</div>
{/snippet}

{#snippet waiting()}
	<p class="mt-1 text-figure text-ink-muted" aria-busy="true">…</p>
{/snippet}

{#snippet problems(errors: string[])}
	{#each errors as error (error)}
		<p class="mt-2 flex items-start gap-1.5 text-small text-ink-secondary" role="status">
			<CircleAlert class="mt-0.5 size-3.5 shrink-0 text-caution" aria-hidden="true" />
			{error}
		</p>
	{/each}
{/snippet}

<Panel title={t('overview.investmentsTitle')}>
	<div class="grid gap-4">
		<section aria-label={returnedLabel()}>
			{#await returned}
				{@render heading(t('overview.twr'), returnsHref, t('overview.performanceLink'))}
				{@render waiting()}
			{:then loaded}
				{@const shown = returnShown(loaded, range)}
				{@render heading(
					shown.span ? t('overview.twrSpan', { span: shown.span }) : t('overview.twr'),
					returnsHref,
					t('overview.performanceLink')
				)}
				{#if shown.state === 'none'}
					<p class="mt-1 text-body text-ink-muted">{t('overview.noInvestmentAccount')}</p>
				{:else if shown.state === 'uninvested'}
					<p class="mt-1 text-body text-ink-muted">{t('overview.nothingInvested')}</p>
				{:else if shown.state === 'measured'}
					<p class={cn('mt-1 text-figure font-medium tabular-nums', tone(shown.twr, 1))}>
						{signedPercent(shown.twr!)}
					</p>
				{/if}
				{#if shown.gain !== null}
					<p class="mt-1 text-small text-ink-secondary">
						{t('overview.gain')}
						<span class={cn('font-medium tabular-nums', tone(shown.gain, 0))}>
							{signed(shown.gain, 0)}
						</span>
						<span class="text-ink-muted">{shown.currency}</span>
					</p>
				{/if}
				{#if shown.rangeNote}
					<p class="mt-0.5 text-caption text-ink-muted">{shown.rangeNote}</p>
				{/if}
				{@render problems(shown.errors)}
			{:catch failure}
				{@render heading(t('overview.twr'), returnsHref, t('overview.performanceLink'))}
				{@render problems([refusal(failure, returnedLabel())])}
			{/await}
		</section>

		<section class="border-t border-line pt-4" aria-label={paidLabel()}>
			{@render heading(t('overview.paidInLast12Months'), incomeHref, t('overview.incomeLink'))}
			{#await paid}
				{@render waiting()}
			{:then loaded}
				{@const shown = incomeShown(loaded)}
				{#if shown.paid}
					{@const { net, tax, currency } = shown.paid}
					<p class="mt-1 text-figure font-medium text-ink">
						<Amount value={net} {currency} places={0} />
					</p>
					<p class="mt-1 text-small text-ink-secondary">
						{t('overview.dividendsAndInterest')}{#if tax}{t('overview.afterTaxBefore')}
							<Amount value={tax} {currency} places={0} />
							{t('overview.afterTaxAfter')}{/if}
					</p>
				{/if}
				{@render problems(shown.errors)}
			{:catch failure}
				{@render problems([refusal(failure, paidLabel())])}
			{/await}
		</section>
	</div>
</Panel>
