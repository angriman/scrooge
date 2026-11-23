<!--
@component
Where statements go in: dropped on it, or chosen from the device. Any number of files of any
source at once; each is recognised on its own.
-->
<script lang="ts">
	import FileUp from '@lucide/svelte/icons/file-up';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { cn } from '#lib/ui/cn.js';

	interface Props {
		/** Whose statements they are, said on it when there are several people. */
		owner: string | null;
		busy: boolean;
		/** Smaller, while statements wait below: it is not the next step then. */
		compact?: boolean;
		onfiles: (files: File[]) => void;
	}

	let { owner, busy, compact = false, onfiles }: Props = $props();

	let over = $state(false);
	let input: HTMLInputElement;

	function dropped(event: DragEvent) {
		event.preventDefault();
		over = false;
		const files = [...(event.dataTransfer?.files ?? [])];
		if (files.length && !busy) onfiles(files);
	}
</script>

<div
	role="region"
	aria-label="Statements to import"
	ondragover={(event) => {
		event.preventDefault();
		over = true;
	}}
	ondragleave={() => (over = false)}
	ondrop={dropped}
	class={cn(
		'flex items-center rounded-lead border-2 border-dashed text-center transition-colors',
		compact ? 'flex-wrap justify-center gap-x-4 gap-y-2 px-4 py-3' : 'flex-col gap-3 px-6 py-10',
		over ? 'border-brand bg-brand-soft' : 'border-line-strong bg-panel'
	)}
>
	{#if busy}
		<LoaderCircle class="size-6 animate-spin text-brand motion-reduce:animate-none" aria-hidden="true" />
		<p class="text-body text-ink" role="status">Reading and reconciling…</p>
	{:else}
		<FileUp class={compact ? 'size-5 text-ink-muted' : 'size-8 text-ink-muted'} aria-hidden="true" />
		<p class={compact ? 'text-body text-ink' : 'text-title text-ink'}>
			Drop statements here{#if owner}{' '}<span class="text-ink-secondary">for {owner}</span>{/if}
		</p>
		<p class={compact ? 'sr-only' : 'max-w-prose text-small text-ink-secondary'}>
			Any number, of any bank or broker. Each is recognised and reconciled on its own, and one
			that fails stops no other. Days already committed are left as they are.
		</p>
		<button
			type="button"
			onclick={() => input.click()}
			class={compact
				? 'h-8 rounded-control border border-line px-3 text-small text-ink hover:border-line-strong'
				: 'mt-1 h-9 rounded-control bg-brand px-4 text-body font-medium text-on-brand hover:bg-brand-hover'}
		>
			Choose files
		</button>
	{/if}
	<input
		bind:this={input}
		type="file"
		multiple
		class="sr-only"
		tabindex="-1"
		aria-hidden="true"
		onchange={(event) => {
			const files = [...(event.currentTarget.files ?? [])];
			event.currentTarget.value = '';
			if (files.length) onfiles(files);
		}}
	/>
</div>
