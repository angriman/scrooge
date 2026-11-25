<!--
@component
One category and its committed rows. An added one can be renamed, an added expense moved under
another top-level category, and an added one taken out while no row carries it; any can be
merged into another of its kind, which its rows and rules then give. What the config gives
rows no rule matches stays as it is.
-->
<script lang="ts">
	import CategorySelect from '#lib/import/CategorySelect.svelte';
	import type { Headed, ListedCategory } from '#lib/types.js';
	import { mergeable, movable, removable, renamable, rowsName } from './categories.js';

	interface Props {
		category: ListedCategory;
		/** What it can be merged into. */
		into: Headed[];
		/** The top-level expense categories it can be moved under. */
		groups: string[];
		/** What the config gives rows no rule matches. */
		unmatched: string | null;
		editable: boolean;
		/** Each answers what went wrong, or null when it was done. */
		onrename: (to: string) => Promise<string | null>;
		onmerge: (into: string) => Promise<string | null>;
		onmove: (under: string) => Promise<string | null>;
		onremove: () => Promise<string | null>;
	}

	let {
		category,
		into,
		groups,
		unmatched,
		editable,
		onrename,
		onmerge,
		onmove,
		onremove
	}: Props = $props();

	let doing = $state<'rename' | 'merge' | 'move' | null>(null);
	let name = $state('');
	let target = $state('');
	let busy = $state(false);
	let problem = $state<string | null>(null);

	const elsewhere = $derived(groups.filter((group) => group !== category.parent));

	function start(what: 'rename' | 'merge' | 'move') {
		doing = what;
		name = category.name;
		target = '';
		problem = null;
	}

	async function act(change: () => Promise<string | null>) {
		busy = true;
		problem = await change();
		busy = false;
		if (!problem) doing = null;
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (doing === 'rename') {
			if (name.trim() === category.name) {
				doing = null;
				return;
			}
			await act(() => onrename(name));
		} else if (doing === 'move' && target) {
			await act(() => onmove(target));
		} else if (doing === 'merge' && target) {
			const gone = category.built_in
				? 'It stays, with nothing in it.'
				: 'It is then taken out.';
			const rows = rowsName(category.rows);
			if (!confirm(`Give ${category.name}'s ${rows} and its rules ${target}? ${gone}`))
				return;
			await act(() => onmerge(target));
		}
	}

	async function remove() {
		if (!confirm(`Remove the category ${category.name}?`)) return;
		await act(onremove);
	}

	const field =
		'h-8 rounded-control border border-line bg-raised px-2 text-small text-ink focus:border-brand focus:outline-none';
	const quiet = 'text-small text-ink-secondary hover:text-ink disabled:opacity-60';
</script>

<li class="py-1.5">
	{#if doing}
		<form onsubmit={submit} class="flex flex-wrap items-center gap-2">
			{#if doing === 'rename'}
				<input
					bind:value={name}
					required
					maxlength="60"
					aria-label={`New name of ${category.name}`}
					class="{field} w-56"
				/>
			{:else if doing === 'move'}
				<span class="text-small text-ink-secondary">Put {category.name} under</span>
				<select bind:value={target} aria-label={`Put ${category.name} under`} class={field}>
					<option value="">Choose a group</option>
					{#if category.parent !== category.name}
						<option value={category.name}>A group of its own</option>
					{/if}
					{#each elsewhere as group (group)}
						{#if group !== category.name}<option value={group}>{group}</option>{/if}
					{/each}
				</select>
			{:else}
				<span class="text-small text-ink-secondary">Merge {category.name} into</span>
				<CategorySelect
					bind:value={target}
					leaves={into}
					label={`Merge ${category.name} into`}
				/>
			{/if}
			<button type="button" class={quiet} onclick={() => (doing = null)}>Cancel</button>
			<button
				type="submit"
				disabled={busy || (doing !== 'rename' && !target)}
				class="h-8 rounded-control bg-brand px-3 text-small font-medium text-on-brand hover:bg-brand-hover disabled:opacity-60"
			>
				{busy ? 'Saving…' : { rename: 'Rename', move: 'Move', merge: 'Merge' }[doing]}
			</button>
		</form>
	{:else}
		<div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
			<span class="min-w-0 flex-1 text-body text-ink">
				{category.name}
				{#if !category.built_in}
					<span class="ml-1 rounded-control bg-brand-soft px-1.5 text-caption text-brand"
						>Added</span
					>
				{/if}
				{#if category.name === unmatched}
					<span class="ml-1 text-caption text-ink-muted" title="scrooge.yaml: unmatched">
						for rows no rule matches
					</span>
				{/if}
			</span>
			<span class="text-caption text-ink-muted tabular-nums">{rowsName(category.rows)}</span>
			{#if editable}
				<span class="flex gap-3">
					{#if renamable(category, unmatched)}
						<button type="button" class={quiet} onclick={() => start('rename')}
							>Rename</button
						>
					{/if}
					{#if movable(category)}
						<button type="button" class={quiet} onclick={() => start('move')}
							>Move</button
						>
					{/if}
					{#if into.length && mergeable(category, unmatched)}
						<button type="button" class={quiet} onclick={() => start('merge')}
							>Merge</button
						>
					{/if}
					{#if removable(category, unmatched)}
						<button type="button" class={quiet} disabled={busy} onclick={remove}
							>Remove</button
						>
					{/if}
				</span>
			{/if}
		</div>
	{/if}
	{#if problem}<p class="mt-1 text-small text-loss" role="alert">{problem}</p>{/if}
</li>
