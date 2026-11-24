<!--
@component
A pattern tried on the committed rows, as a rule with it would match them: how many, and the
newest of them. Nothing is saved.
-->
<script lang="ts">
	import { day } from '#lib/format/date.js';
	import type { PatternTest } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import { testQuery } from './rules.js';

	interface Props {
		/** Whose rows: everyone's when empty. */
		owner: string;
		/** The token of these pages, which a pattern tried needs. */
		token: string;
		match?: string;
		direction?: string;
	}

	let { owner, token, match = $bindable(''), direction = $bindable('') }: Props = $props();

	let found = $state<PatternTest | null>(null);
	let trying = $state(false);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		await run();
	}

	/** Try what the box holds now. */
	export async function run() {
		if (!match.trim()) return;
		trying = true;
		try {
			const response = await fetch(`/api/settings/rules/test?${testQuery(match, direction, owner)}`, {
				headers: { 'x-token': token }
			});
			found = await response.json();
		} catch {
			found = { count: 0, rows: [], errors: ['The app could not be reached: try again.'] };
		}
		trying = false;
	}

	const field =
		'h-8 rounded-control border border-line bg-raised px-2 text-small text-ink focus:border-brand focus:outline-none';
	const rows = (count: number) => (count === 1 ? '1 committed row' : `${count} committed rows`);
</script>

<form onsubmit={submit} class="flex flex-wrap items-end gap-3">
	<label class="flex min-w-0 flex-1 flex-col gap-1 text-caption text-ink-secondary">
		Pattern to try
		<input
			bind:value={match}
			required
			maxlength="200"
			placeholder="KONSUM|FRISCHMARKT"
			spellcheck="false"
			class="{field} w-full font-mono"
		/>
	</label>
	<label class="flex flex-col gap-1 text-caption text-ink-secondary">
		Money
		<select bind:value={direction} class={field}>
			<option value="">In and out</option>
			<option value="in">In</option>
			<option value="out">Out</option>
		</select>
	</label>
	<button
		type="submit"
		disabled={trying}
		class="h-8 rounded-control border border-line px-3 text-small text-ink hover:border-line-strong disabled:opacity-60"
	>
		{trying ? 'Trying…' : 'Try'}
	</button>
</form>

{#if found}
	<div class="mt-3" aria-live="polite">
		{#each found.errors as error (error)}
			<p class="text-small text-loss" role="alert">{error}</p>
		{/each}
		{#if !found.errors.length}
			<p class="text-small text-ink-secondary">
				Matches {rows(found.count)}{found.count > found.rows.length ? `; the newest ${found.rows.length}:` : found.count ? ':' : '.'}
			</p>
			{#if found.rows.length}
				<ul class="mt-2 max-h-80 divide-y divide-line overflow-y-auto">
					{#each found.rows as row (row.id)}
						<li class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-x-3 py-1.5 text-small">
							<span class="text-ink-muted tabular-nums">{day(row.date)}</span>
							<span class="min-w-0 truncate text-ink" title={row.description}>
								{row.counterparty || row.description}
								<span class="text-ink-muted">· {row.owner} · {row.category ?? 'no category'}</span>
							</span>
							<Amount value={row.amount} currency={row.currency} class="text-ink" />
						</li>
					{/each}
				</ul>
			{/if}
		{/if}
	</div>
{/if}
