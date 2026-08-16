import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ExternalSearchSourceChips from './ExternalSearchSourceChips.vue'

describe('ExternalSearchSourceChips', () => {
  it('固定显示真实站点图标，并保留南+未命中状态和搜索动作', async () => {
    const wrapper = mount(ExternalSearchSourceChips, {
      props: {
        item: {
          canonical_rjcode: 'RJ01576821',
          external_search: {
            anime_share: { status: 'loading', results: [], search_results: [] },
            south_plus: { status: 'miss', results: [], search_results: [] },
          },
        },
      },
    })

    const buttons = wrapper.findAll('.external-source-chip')
    expect(buttons).toHaveLength(3)
    expect(buttons[0].get('img').attributes('src')).toContain('anime-sharing')
    expect(buttons[1].get('img').attributes('src')).toContain('south-plus')
    expect(buttons[1].classes()).toContain('is-miss')
    expect(buttons[1].attributes('title')).toContain('未找到')

    await buttons[1].trigger('click')
    expect(wrapper.emitted('open')?.[0]?.[0]).toMatchObject({
      source: 'south_plus',
      status: 'miss',
    })
    expect(wrapper.emitted('open')?.[0]?.[0]?.results?.[0]?.url).toContain('keyword=RJ01576821')
  })

  it('asmr.one 标签复用 has_asmr_one 探测结果并跳转作品页', async () => {
    const wrapper = mount(ExternalSearchSourceChips, {
      props: {
        item: {
          canonical_rjcode: 'RJ01576821',
          has_asmr_one: true,
          asmr_available_rjcode: 'RJ01576822',
          external_search: {
            anime_share: { status: 'miss', results: [], search_results: [] },
            south_plus: { status: 'miss', results: [], search_results: [] },
          },
        },
      },
    })

    const buttons = wrapper.findAll('.external-source-chip')
    expect(buttons).toHaveLength(3)
    const asmrChip = buttons[2]
    expect(asmrChip.get('img').attributes('src')).toContain('asmr-one')
    expect(asmrChip.classes()).toContain('is-hit')
    expect(asmrChip.attributes('title')).toContain('命中')

    await asmrChip.trigger('click')
    const payload = wrapper.emitted('open')?.[0]?.[0]
    expect(payload).toMatchObject({ source: 'asmr_one', status: 'hit' })
    expect(payload.results[0].url).toBe('https://asmr.one/works/RJ01576822')
  })

  it('asmr.one 未命中时仍可跳转 canonical 作品页', async () => {
    const wrapper = mount(ExternalSearchSourceChips, {
      props: {
        item: {
          canonical_rjcode: 'RJ01576821',
          has_asmr_one: false,
          external_search: {
            anime_share: { status: 'miss', results: [], search_results: [] },
            south_plus: { status: 'miss', results: [], search_results: [] },
          },
        },
      },
    })

    const asmrChip = wrapper.findAll('.external-source-chip')[2]
    expect(asmrChip.classes()).toContain('is-miss')
    await asmrChip.trigger('click')
    const payload = wrapper.emitted('open')?.[0]?.[0]
    expect(payload.results[0].url).toBe('https://asmr.one/works/RJ01576821')
  })
})
