<!--
@component
Where the money came from: work, investments and the rest, each with its sources, the largest
first. A source opens its transactions over the same range.
-->
<script lang="ts">
	import { percentOf, percent, sum } from '#lib/format/number.js';
	import type { CashFlowReport } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import { GROUPS, incomeColor } from './names.js';

	interface Props {
		report: CashFlowReport;
		transactions: (changes: Record<string, string>) => string;
	}

	let { report, transactions }: Props = $props();

	const groups = $derived(
		GROUPS.map((group) => {
			const sources = report.sources.filter((source) => source.group === group);
			return { group, sources, total: sum(sources.map((source) => source.total)) };
		}).filter((group) => group.sources.length)
	);
	const all = $derived(sum(groups.map((group) => group.total)));
</script>

<Panel title="Where the money came from">
	{#if !groups.length}
		<p class="text-body text-ink-secondary">Nothing came in over these months.</p>
	{/if}
	<div class="space-y-4">
		{#each groups as { group, sources, total } (group)}
			{@const share = percentOf(total, all, 1)}
			<div>
				<p class="flex items-baseline gap-2 text-body text-ink">
					<span class="size-2 shrink-0 self-center rounded-[2px]" style:background={incomeColor(group)}></span>
					<span class="flex-1 font-medium">{group}</span>
					{#if share !== null}<span class="text-caption text-ink-muted">{percent(share, 0)}</span>{/if}
					<Amount value={total} places={0} />
				</p>
				<ul class="mt-1 pl-4">
					{#each sources as source (source.name)}
						<li>
							<a
								href={transactions({ category: source.name })}
								class="flex items-baseline gap-3 rounded-control px-1 py-1 text-small hover:bg-sunken"
							>
								<span class="min-w-0 flex-1 truncate text-ink-secondary">{source.name}</span>
								<span class="text-caption text-ink-muted">{source.count}×</span>
								<Amount value={source.total} places={0} class={source.total.startsWith('-') ? 'text-loss' : 'text-ink'} />
							</a>
						</li>
					{/each}
				</ul>
			</div>
		{/each}
	</div>
</Panel>
