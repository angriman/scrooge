<!--
@component
A block of a page: a title, what it shows, the controls that change it, and its content.
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from './cn.js';

	interface Props {
		title?: string;
		/** A line under the title: what the figures are, as of when. */
		note?: string;
		/** Controls at the right of the title (a range, a breakdown). */
		actions?: Snippet;
		children: Snippet;
		class?: string;
	}

	let { title, note, actions, children, class: className }: Props = $props();
</script>

<section class={cn('rounded-panel bg-panel p-4', className)}>
	{#if title || actions}
		<header class="mb-3 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
			<div class="min-w-0">
				{#if title}<h2 class="text-title font-medium text-ink">{title}</h2>{/if}
				{#if note}<p class="text-small text-ink-secondary">{note}</p>{/if}
			</div>
			{#if actions}<div class="flex flex-wrap items-center gap-2">
					{@render actions()}
				</div>{/if}
		</header>
	{/if}
	{@render children()}
</section>
