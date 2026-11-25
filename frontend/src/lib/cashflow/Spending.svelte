<!--
@component
Where the money went, by category, the largest first: what each took over the range, per
month, and against its usual month and the same months a year before. Below them, the places
most was spent at. Each opens its transactions over the same range.
-->
<script lang="ts">
	import { approximate, difference, percentOf, signedPercent } from '#lib/format/number.js';
	import type { CashFlowReport } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import { cn } from '#lib/ui/cn.js';

	interface Props {
		report: CashFlowReport;
		colors: Map<string, string>;
		/** The address of the transactions with `changes` to the range shown. */
		transactions: (changes: Record<string, string>) => string;
	}

	let { report, colors, transactions }: Props = $props();

	const largest = $derived(Math.max(1, ...report.categories.map((c) => approximate(c.spent))));
	/** How far `now` is from `then`, in percent of `then`; null when there is nothing to say. */
	const against = (now: string, then: string | null) => {
		if (then === null || approximate(then) <= 0) return null;
		return percentOf(difference(now, then), then, 0);
	};
	const tone = (change: string | null) =>
		change === null
			? ''
			: approximate(change) > 10
				? 'text-caution'
				: approximate(change) < -10
					? 'text-gain'
					: 'text-ink-secondary';
	let merchants = $state(false);
</script>

<Panel
	title="Where the money went"
	note="Per month, against the usual month and the same months a year before."
>
	<ul class="divide-y divide-line">
		{#each report.categories as category (category.name)}
			{@const usual = against(category.monthly, category.usual)}
			{@const before = against(category.spent, category.year_before)}
			<li>
				<a
					href={transactions({ category: category.name })}
					class="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 rounded-control px-1 py-2 hover:bg-sunken"
				>
					<span class="flex min-w-0 items-center gap-2 text-body text-ink">
						<span
							class="size-2 shrink-0 rounded-[2px]"
							style:background={colors.get(category.name) ?? 'var(--data-other)'}
						></span>
						<span class="truncate">{category.name}</span>
					</span>
					<Amount value={category.spent} places={0} class="text-right text-body text-ink" />
					<span class="h-1.5 self-center rounded-full bg-sunken" aria-hidden="true">
						<span
							class="block h-full rounded-full"
							style:width="{Math.max(1.5, (approximate(category.spent) / largest) * 100)}%"
							style:background={colors.get(category.name) ?? 'var(--data-other)'}
						></span>
					</span>
					<span class="flex justify-end gap-3 text-caption whitespace-nowrap text-ink-muted">
						<span
							><Amount value={category.monthly} places={0} class="text-ink-secondary" /> a month</span
						>
						{#if usual !== null}<span class={tone(usual)}>{signedPercent(usual, 0)} usual</span
							>{/if}
						{#if before !== null}<span class={cn('hidden sm:inline', tone(before))}
								>{signedPercent(before, 0)} a year before</span
							>{/if}
					</span>
				</a>
			</li>
		{/each}
	</ul>

	{#if report.merchants.length}
		<button
			type="button"
			onclick={() => (merchants = !merchants)}
			aria-expanded={merchants}
			class="mt-3 text-small text-brand hover:text-brand-hover"
		>
			{merchants ? 'Hide' : 'Show'} the {report.merchants.length} places most was spent at
		</button>
		{#if merchants}
			<ul class="mt-2 divide-y divide-line">
				{#each report.merchants as merchant (merchant.name)}
					<li>
						<a
							href={transactions({ merchant: merchant.name })}
							class="flex items-baseline gap-3 rounded-control px-1 py-1.5 hover:bg-sunken"
						>
							<span class="min-w-0 flex-1 truncate text-body text-ink">{merchant.name}</span>
							<span class="hidden text-caption text-ink-muted sm:inline"
								>{merchant.category}, {merchant.count}×</span
							>
							<Amount value={merchant.spent} places={0} class="text-body text-ink" />
						</a>
					</li>
				{/each}
			</ul>
			{#if report.other_merchants.count}
				<p class="mt-2 text-caption text-ink-muted">
					And {report.other_merchants.count} more, <Amount
						value={report.other_merchants.spent}
						places={0}
					/> together.
				</p>
			{/if}
		{/if}
	{/if}
</Panel>
