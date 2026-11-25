<!--
@component
A share of a whole as a bar on its track: how much of everything a holding is.
-->
<script lang="ts">
	import { approximate } from '#lib/format/number.js';
	import type { Amount } from '#lib/types.js';
	import { cn } from './cn.js';

	interface Props {
		/** In percent of the whole (`"42.33"`); a bar as long as the longest is `scale`. */
		share: Amount | null;
		color: string;
		/** The percent the full track stands for: the largest share of the list, so that the
		 * bars are told apart. */
		scale?: number;
		class?: string;
	}

	let { share, color, scale = 100, class: className }: Props = $props();

	const width = $derived(
		share === null ? 0 : Math.max(0, Math.min(100, (approximate(share) / scale) * 100))
	);
</script>

<span
	class={cn('block h-1.5 overflow-hidden rounded-mark bg-sunken', className)}
	aria-hidden="true"
>
	<span class="block h-full rounded-mark" style:width="{width}%" style:background-color={color}
	></span>
</span>
