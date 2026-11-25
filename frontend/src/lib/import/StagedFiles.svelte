<!--
@component
The statements staged for one person: what each is, the days it covers and its rows, with
a way to remove it. Below them, what stands in the way of committing them, if anything.
-->
<script lang="ts">
	import Trash from '@lucide/svelte/icons/trash-2';
	import { day } from '#lib/format/date.js';
	import type { Staged } from '#lib/types.js';
	import Panel from '#lib/ui/Panel.svelte';

	interface Props {
		files: Staged['files'];
		blocked: string[];
		/** The id of the statement being removed, if one is. */
		removing: number | null;
		onremove: (file: Staged['files'][number]) => void;
	}

	let { files, blocked, removing, onremove }: Props = $props();
</script>

<Panel title="Staged" note="Not counted anywhere until they are committed.">
	<ul class="divide-y divide-line">
		{#each files as file (file.id)}
			<li class="flex items-center gap-3 py-2">
				<span class="min-w-0 flex-1">
					<span class="block truncate text-body text-ink">{file.label}</span>
					<span class="block truncate text-small text-ink-secondary" title={file.filename}>
						{day(file.first)} to {day(file.last)}, {file.transactions}
						{file.transactions === 1 ? 'row' : 'rows'}{#if !file.checked}, nothing to check against{/if}
					</span>
				</span>
				<button
					type="button"
					onclick={() => onremove(file)}
					disabled={removing !== null}
					aria-label="Remove {file.filename}"
					title="Remove {file.filename}"
					class="grid size-8 shrink-0 place-items-center rounded-control text-ink-muted hover:bg-sunken hover:text-loss disabled:opacity-50"
				>
					<Trash class="size-4" aria-hidden="true" />
				</button>
			</li>
		{/each}
	</ul>
	{#if blocked.length}
		<div class="mt-3 rounded-control bg-caution-soft px-3 py-2">
			<p class="text-small font-medium text-ink">Not ready to commit</p>
			<ul class="mt-1 list-disc space-y-0.5 pl-4 text-small text-ink-secondary">
				{#each blocked as reason (reason)}<li>{reason}.</li>{/each}
			</ul>
		</div>
	{/if}
</Panel>
