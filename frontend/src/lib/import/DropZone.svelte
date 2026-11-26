<!--
@component
Where statements go in: dropped on it, or chosen from the device. Any number of files of any
source at once, whole folders or zips; each file is recognised on its own.
-->
<script lang="ts">
	import FileUp from '@lucide/svelte/icons/file-up';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { t } from '#lib/i18n/index.svelte.js';
	import { cn } from '#lib/ui/cn.js';
	import { droppedFiles, shown } from './dropped.js';

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
	let folders: HTMLInputElement;

	async function dropped(event: DragEvent) {
		event.preventDefault();
		over = false;
		if (busy || !event.dataTransfer) return;
		const files = await droppedFiles(event.dataTransfer);
		if (files.length) onfiles(files);
	}

	function chosen(event: Event & { currentTarget: HTMLInputElement }) {
		const files = [...(event.currentTarget.files ?? [])].filter(shown);
		event.currentTarget.value = '';
		if (files.length) onfiles(files);
	}
</script>

<div
	role="region"
	aria-label={t('import.dropRegionAria')}
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
		<LoaderCircle
			class="size-6 animate-spin text-brand motion-reduce:animate-none"
			aria-hidden="true"
		/>
		<p class="text-body text-ink" role="status">{t('import.readingReconciling')}</p>
	{:else}
		<FileUp
			class={compact ? 'size-5 text-ink-muted' : 'size-8 text-ink-muted'}
			aria-hidden="true"
		/>
		<p class={compact ? 'text-body text-ink' : 'text-title text-ink'}>
			{t('import.dropTitle')}{#if owner}{' '}<span class="text-ink-secondary"
					>{t('import.dropForOwner', { owner })}</span
				>{/if}
		</p>
		<p class={compact ? 'sr-only' : 'max-w-prose text-small text-ink-secondary'}>
			{t('import.dropHint')}
		</p>
		<div class={cn('flex flex-wrap justify-center gap-2', !compact && 'mt-1')}>
			<button
				type="button"
				onclick={() => input.click()}
				class={compact
					? 'h-11 rounded-control border border-line px-3 text-small text-ink hover:border-line-strong sm:h-8'
					: 'h-11 rounded-control bg-brand px-4 text-body font-medium text-on-brand hover:bg-brand-hover sm:h-9'}
			>
				{t('import.chooseFiles')}
			</button>
			<button
				type="button"
				onclick={() => folders.click()}
				class={compact
					? 'h-11 rounded-control border border-line px-3 text-small text-ink hover:border-line-strong sm:h-8'
					: 'h-11 rounded-control border border-line px-4 text-body text-ink hover:border-line-strong sm:h-9'}
			>
				{t('import.chooseFolder')}
			</button>
		</div>
	{/if}
	<input
		bind:this={input}
		type="file"
		multiple
		class="sr-only"
		tabindex="-1"
		aria-hidden="true"
		onchange={chosen}
	/>
	<input
		bind:this={folders}
		type="file"
		webkitdirectory
		class="sr-only"
		tabindex="-1"
		aria-hidden="true"
		onchange={chosen}
	/>
</div>
