<!--
@component
Net worth at the end of every month, newest first, in several currencies side by side: for each
its total, how it moved since the month before and by how much in percent. What accounts
brought when they came into view is left out of a change. On a phone the table scrolls
sideways, the months staying in sight.
-->
<script lang="ts">
	import { day } from '#lib/format/date.js';
	import type { NetWorthMonths } from '#lib/types.js';
	import { cn } from '#lib/ui/cn.js';
	import { CHANGE_NOTE, COLUMNS, table, type Way } from './monthEnds.js';

	interface Props {
		months: Promise<NetWorthMonths>;
		/** The currency of the header: its columns come first. */
		currency: string;
		class?: string;
	}

	let { months, currency, class: className }: Props = $props();

	const colour: Record<Way, string> = {
		gain: 'text-gain',
		loss: 'text-loss',
		flat: 'text-ink-muted',
		none: 'text-ink'
	};
</script>

<div class={className}>
	{#await months}
		<p class="py-16 text-center text-small text-ink-muted" role="status">Working out the month-ends…</p>
	{:then sent}
		{@const shown = table(sent, currency)}
		{#each sent.errors as error, n (n)}
			<p class="px-3 py-2 text-small text-caution" role="status">{error}</p>
		{/each}
		{#if shown.rows.length}
			<div class="max-h-[28rem] overflow-auto">
				<table class="w-full min-w-max border-separate border-spacing-0 text-small tabular-nums">
					<thead class="sticky top-0 z-20 bg-panel text-caption text-ink-muted">
						<tr>
							<th
								rowspan="2"
								scope="col"
								class="sticky left-0 z-10 border-b border-line bg-panel py-1.5 pr-4 pl-3 text-left align-bottom font-normal"
							>
								Month
							</th>
							{#each shown.currencies as code (code)}
								<th
									colspan={COLUMNS.length}
									scope="colgroup"
									class="border-l border-line px-3 pt-1.5 text-left font-medium text-ink-secondary"
								>
									{code}
								</th>
							{/each}
						</tr>
						<tr>
							{#each shown.currencies as code (code)}
								{#each COLUMNS as column, n (column.key)}
									<th
										scope="col"
										class={cn(
											'border-b border-line px-3 py-1.5 text-right font-normal whitespace-nowrap',
											n === 0 && 'border-l'
										)}
										title={column.key === 'total' ? undefined : CHANGE_NOTE}
									>
										{column.label}
									</th>
								{/each}
							{/each}
						</tr>
					</thead>
					<tbody>
						{#each shown.rows as row (row.month)}
							<tr class="group hover:bg-raised">
								<th
									scope="row"
									class="sticky left-0 z-10 border-b border-line bg-panel py-1.5 pr-4 pl-3 text-left font-normal whitespace-nowrap text-ink-secondary group-hover:bg-raised"
								>
									<span class="sm:hidden">{row.short}</span>
									<span class="hidden sm:inline">{row.label}</span>
									{#if row.partial}
										<span class="ml-1 text-caption text-ink-muted" title="Up to {day(row.day)}">
											to date
										</span>
									{/if}
								</th>
								{#each row.groups as group, g (shown.currencies[g])}
									{#each group as cell, n (COLUMNS[n].key)}
										<td
											class={cn(
												'border-b border-line px-3 py-1.5 text-right whitespace-nowrap',
												n === 0 && 'border-l',
												colour[cell.way]
											)}
										>
											{cell.text}
										</td>
									{/each}
								{/each}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<p class="px-3 pt-2 text-caption text-ink-muted">{CHANGE_NOTE}</p>
		{:else if !sent.errors.length}
			<p class="py-16 text-center text-small text-ink-muted">No month to show yet.</p>
		{/if}
	{:catch}
		<p class="py-16 text-center text-small text-ink-muted" role="status">
			The month-ends could not be loaded: reload the page to try again.
		</p>
	{/await}
</div>
