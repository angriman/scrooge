<!--
@component
The range as one flow: where the money came from on the left, all of it together in the
middle, and where it went on the right, the categories and what was saved, each as thick as
its amount. When more went out than came in, the difference comes from savings.
-->
<script lang="ts">
	import { Chart, Svg } from 'layerchart';
	import { Sankey } from 'layerchart/graph';
	import { approximate, short } from '#lib/format/number.js';
	import type { CashFlowReport } from '#lib/types.js';
	import { GROUPS, incomeColor, OTHERS } from './names.js';

	interface Props {
		report: CashFlowReport;
		/** The colour of each category, as the bars have it. */
		colors: Map<string, string>;
	}

	let { report, colors }: Props = $props();

	const SHOWN = 8; // categories with a band of their own; the rest go together
	const IN = 'In';
	const SAVED = 'Saved';
	const DRAWN = 'From savings';

	const graph = $derived.by(() => {
		const earned = new Map<string, number>();
		// A source can lose money (trading): it counts against its group, which is drawn only
		// when it comes to more than nothing.
		for (const source of report.sources) {
			earned.set(source.group, (earned.get(source.group) ?? 0) + approximate(source.total));
		}
		for (const [group, value] of earned) earned.set(group, Math.max(0, value));
		const spent = report.categories.map((c) => ({
			name: c.name,
			value: Math.max(0, approximate(c.spent))
		}));
		const rest = spent.slice(SHOWN).reduce((sum, c) => sum + c.value, 0);
		const outs = [
			...spent.slice(0, SHOWN),
			...(rest > 0 ? [{ name: OTHERS, value: rest }] : [])
		].filter((c) => c.value > 0);
		const income = [...earned.values()].reduce((sum, v) => sum + v, 0);
		const out = outs.reduce((sum, c) => sum + c.value, 0);
		// What was saved is the lead's figure: income less spending once paid back.
		const saved = approximate(report.totals?.saved ?? '0');
		const nodes = [
			...GROUPS.filter((g) => (earned.get(g) ?? 0) > 0).map((g) => ({
				id: g,
				label: g,
				fill: incomeColor(g)
			})),
			...(out > income ? [{ id: DRAWN, label: DRAWN, fill: 'var(--caution)' }] : []),
			{ id: IN, label: 'Came in', fill: 'var(--ink-muted)' },
			...outs.map((c) => ({
				id: c.name,
				label: c.name,
				fill: colors.get(c.name) ?? 'var(--data-other)'
			})),
			...(saved > 0 ? [{ id: SAVED, label: SAVED, fill: 'var(--brand)' }] : [])
		];
		const links = [
			...GROUPS.filter((g) => (earned.get(g) ?? 0) > 0).map((g) => ({
				source: g,
				target: IN,
				value: earned.get(g)!
			})),
			...(out > income ? [{ source: DRAWN, target: IN, value: out - income }] : []),
			...outs.map((c) => ({ source: IN, target: c.name, value: c.value })),
			...(saved > 0 ? [{ source: IN, target: SAVED, value: saved }] : [])
		];
		return { nodes, links };
	});
	const fill = (id: string) =>
		graph.nodes.find((node) => node.id === id)?.fill ?? 'var(--data-other)';
</script>

<!-- Narrower than its labels need, it scrolls sideways rather than squeeze the bands. -->
<div class="-mx-4 overflow-x-auto px-4">
	<div
		class="h-[340px] w-full min-w-[34rem]"
		role="img"
		aria-label="Where the money came from and where it went"
	>
		<Chart data={graph} padding={{ left: 120, right: 140, top: 6, bottom: 6 }}>
			<Svg>
				<Sankey nodeId={(d) => d.id} nodeWidth={8} nodePadding={12} nodeAlign="justify">
					{#snippet children({ nodes, links })}
						{#each links as link, index (index)}
							{@const from = link.source.x1 ?? 0}
							{@const to = link.target.x0 ?? 0}
							{@const middle = (from + to) / 2}
							<path
								d="M{from},{link.y0} C{middle},{link.y0} {middle},{link.y1} {to},{link.y1}"
								fill="none"
								stroke={fill(
									link.target.id === IN ? link.source.id : link.target.id
								)}
								stroke-opacity="0.35"
								stroke-width={Math.max(1, link.width ?? 1)}
							/>
						{/each}
						{#each nodes as node (node.id)}
							{@const left = node.depth === 0}
							<rect
								x={node.x0}
								y={node.y0}
								width={(node.x1 ?? 0) - (node.x0 ?? 0)}
								height={Math.max(1, (node.y1 ?? 0) - (node.y0 ?? 0))}
								fill={node.fill}
								rx="2"
							/>
							<text
								x={left ? (node.x0 ?? 0) - 6 : (node.x1 ?? 0) + 6}
								y={((node.y0 ?? 0) + (node.y1 ?? 0)) / 2}
								dy="0.35em"
								text-anchor={left ? 'end' : 'start'}
								class="fill-ink text-caption"
							>
								{node.label}
								{#if node.id !== IN}<tspan class="fill-ink-muted"
										>{short(String(Math.round(node.value ?? 0)))}</tspan
									>{/if}
							</text>
						{/each}
					{/snippet}
				</Sankey>
			</Svg>
		</Chart>
	</div>
</div>
