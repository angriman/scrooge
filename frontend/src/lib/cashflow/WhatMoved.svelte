<!--
@component
What moved: the three categories furthest from their usual month over the range, each with what
it took a month against the usual month, and a way to its transactions; a change within a tenth
of the usual month is not coloured. Nothing shows when no category has a usual month.
-->
<script lang="ts">
	import { signed, signedPercent } from '#lib/format/number.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { toneOf, type Moved } from './whatMoved.js';

	interface Props {
		/** From `whatMoved`. */
		moved: Moved[];
		colors: Map<string, string>;
		/** The address of the transactions with `changes` to the range shown. */
		transactions: (changes: Record<string, string>) => string;
	}

	let { moved, colors, transactions }: Props = $props();

	const TONES = { above: 'text-caution', below: 'text-gain' } as const;
</script>

{#if moved.length}
	<Panel title="What moved" note="The categories furthest from their usual month, per month.">
		<ul class="divide-y divide-line">
			{#each moved as each (each.name)}
				{@const tone = toneOf(each)}
				<li>
					<a
						href={transactions({ category: each.name })}
						class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-0.5 rounded-control px-1 py-2 hover:bg-sunken"
					>
						<span class="flex min-w-0 items-center gap-2 text-body text-ink">
							<span
								class="size-2 shrink-0 rounded-[2px]"
								style:background={colors.get(each.name) ?? 'var(--data-other)'}
							></span>
							<span class="truncate">{each.name}</span>
						</span>
						<span
							class={cn(
								'text-right text-body tabular-nums whitespace-nowrap',
								tone ? TONES[tone] : 'text-ink-secondary'
							)}
						>
							{signed(each.change, 0)}{#if each.percent !== null}<span class="ml-1 text-small"
									>({signedPercent(each.percent, 0)})</span
								>{/if}
						</span>
						<span class="col-span-2 pl-4 text-caption text-ink-muted">
							<Amount value={each.monthly} places={0} class="text-ink-secondary" /> a month, usually
							<Amount value={each.usual} places={0} class="text-ink-secondary" />
						</span>
					</a>
				</li>
			{/each}
		</ul>
	</Panel>
{/if}
