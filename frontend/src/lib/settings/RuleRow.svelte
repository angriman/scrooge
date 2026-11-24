<!--
@component
One rule: what it looks for, what it gives, and how many committed rows it matches. Where the
app can change it, it is edited in place, moved up or down in its file, or taken out.
-->
<script lang="ts">
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Trash from '@lucide/svelte/icons/trash-2';
	import CategorySelect from '#lib/import/CategorySelect.svelte';
	import type { Headed, Rule } from '#lib/types.js';
	import { changes, directionName, draftOf, matchesName, type Draft } from './rules.js';

	interface Props {
		rule: Rule;
		leaves: Headed[];
		editable: boolean;
		first: boolean;
		last: boolean;
		/** Each answers what went wrong, or null when it was done. */
		onsave: (draft: Draft) => Promise<string | null>;
		onmove: (by: -1 | 1) => Promise<string | null>;
		onremove: () => Promise<string | null>;
		/** Try its pattern (as being edited) on the committed rows. */
		ontry: (match: string, direction: string) => void;
	}

	let { rule, leaves, editable, first, last, onsave, onmove, onremove, ontry }: Props = $props();

	let editing = $state(false);
	let draft = $state<Draft>({ match: '', category: '', direction: '' });
	let busy = $state(false);
	let problem = $state<string | null>(null);

	function edit() {
		draft = draftOf(rule);
		problem = null;
		editing = true;
	}

	async function act(change: () => Promise<string | null>) {
		busy = true;
		problem = await change();
		busy = false;
		if (!problem) editing = false;
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (!changes(rule, draft)) {
			editing = false;
			return;
		}
		await act(() => onsave(draft));
	}

	async function remove() {
		if (!confirm(`Remove the rule ${rule.match} → ${rule.category}? Rows committed keep their category.`)) return;
		await act(onremove);
	}

	const field =
		'h-8 rounded-control border border-line bg-raised px-2 text-small text-ink focus:border-brand focus:outline-none';
	const icon =
		'inline-flex size-7 items-center justify-center rounded-control text-ink-secondary hover:bg-sunken hover:text-ink disabled:opacity-40 disabled:hover:bg-transparent';
</script>

<li class="py-2">
	{#if editing}
		<form onsubmit={save} class="flex flex-wrap items-end gap-2">
			<label class="flex min-w-0 flex-1 basis-60 flex-col gap-1 text-caption text-ink-secondary">
				Pattern
				<input bind:value={draft.match} required spellcheck="false" class="{field} w-full font-mono" />
			</label>
			<label class="flex flex-col gap-1 text-caption text-ink-secondary">
				Category
				<CategorySelect bind:value={draft.category} {leaves} label={`Category of the rule ${rule.match}`} />
			</label>
			<label class="flex flex-col gap-1 text-caption text-ink-secondary">
				Money
				<select bind:value={draft.direction} class={field}>
					<option value="">In and out</option>
					<option value="in">In</option>
					<option value="out">Out</option>
				</select>
			</label>
			<div class="flex gap-2">
				<button
					type="button"
					onclick={() => ontry(draft.match, draft.direction)}
					class="h-8 rounded-control border border-line px-3 text-small text-ink-secondary hover:text-ink"
				>
					Try
				</button>
				<button
					type="button"
					onclick={() => (editing = false)}
					class="h-8 rounded-control border border-line px-3 text-small text-ink-secondary hover:text-ink"
				>
					Cancel
				</button>
				<button
					type="submit"
					disabled={busy || !draft.category}
					class="h-8 rounded-control bg-brand px-3 text-small font-medium text-on-brand hover:bg-brand-hover disabled:opacity-60"
				>
					{busy ? 'Saving…' : 'Save'}
				</button>
			</div>
		</form>
	{:else}
		<div class="flex items-center gap-3">
			<span class="w-6 shrink-0 text-right text-caption text-ink-muted tabular-nums">{rule.position}</span>
			<div class="min-w-0 flex-1">
				<p class="flex min-w-0 flex-wrap items-baseline gap-x-2 text-body">
					<code class="min-w-0 truncate font-mono text-small text-ink" title={rule.match}>{rule.match}</code>
					<span class="text-ink-muted" aria-hidden="true">→</span>
					<span class="text-ink">{rule.category}</span>
				</p>
				<p class="text-caption text-ink-muted">
					{directionName(rule.direction)}{rule.account ? ` · ${rule.account} only` : ''} ·
					<span class={rule.matches === null ? 'text-caution' : ''}>{matchesName(rule.matches)}</span>
				</p>
			</div>
			<button type="button" class={icon} aria-label={`Try ${rule.match}`} title="Try its pattern" onclick={() => ontry(rule.match, rule.direction)}>
				<span class="text-caption">Try</span>
			</button>
			{#if editable}
				<button type="button" class={icon} aria-label={`Edit the rule ${rule.match}`} title="Edit" onclick={edit}>
					<Pencil class="size-4" aria-hidden="true" />
				</button>
				<button type="button" class={icon} aria-label={`Move ${rule.match} up`} title="Tried earlier" disabled={first || busy} onclick={() => act(() => onmove(-1))}>
					<ArrowUp class="size-4" aria-hidden="true" />
				</button>
				<button type="button" class={icon} aria-label={`Move ${rule.match} down`} title="Tried later" disabled={last || busy} onclick={() => act(() => onmove(1))}>
					<ArrowDown class="size-4" aria-hidden="true" />
				</button>
				<button type="button" class={icon} aria-label={`Remove the rule ${rule.match}`} title="Remove" disabled={busy} onclick={remove}>
					<Trash class="size-4" aria-hidden="true" />
				</button>
			{/if}
		</div>
	{/if}
	{#if problem}<p class="mt-1 text-small text-loss" role="alert">{problem}</p>{/if}
</li>
