<!--
@component
One thing that waits for the reader, on a line of its own: what it is, and the action that
deals with it. `waiting` is work to do (statements to commit); `caution` is something off
(an account behind, figures that differ).
-->
<script lang="ts">
	import type { Component, Snippet } from 'svelte';
	import { cn } from './cn.js';

	interface Props {
		tone: 'waiting' | 'caution';
		icon: Component<{ class?: string; 'aria-hidden'?: boolean | 'true' }>;
		/** What to do about it, and where that is done. */
		action?: { label: string; href: string; reload?: boolean };
		children: Snippet;
		class?: string;
	}

	let { tone, icon: Icon, action, children, class: className }: Props = $props();
</script>

<div
	class={cn(
		'flex flex-wrap items-center gap-x-3 gap-y-1 rounded-panel border px-4 py-2.5 text-body text-ink',
		tone === 'waiting' && 'border-brand/30 bg-brand-soft',
		tone === 'caution' && 'border-caution/30 bg-caution-soft',
		className
	)}
>
	<Icon
		class={cn('size-4 shrink-0', tone === 'waiting' ? 'text-brand' : 'text-caution')}
		aria-hidden="true"
	/>
	<span class="min-w-0 flex-1">{@render children()}</span>
	{#if action}
		<a
			href={action.href}
			data-sveltekit-reload={action.reload ? '' : undefined}
			class="-my-2.5 inline-flex min-h-11 items-center text-small font-medium whitespace-nowrap text-brand hover:text-brand-hover sm:my-0 sm:min-h-0"
		>
			{action.label}
		</a>
	{/if}
</div>
