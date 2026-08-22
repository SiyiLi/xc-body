#include <esp_app_desc.h>
#include <sdkconfig.h>

const __attribute__((section(".rodata_desc"))) esp_app_desc_t esp_app_desc = {
    .magic_word = ESP_APP_DESC_MAGIC_WORD,
#ifdef CONFIG_BOOTLOADER_APP_SECURE_VERSION
    .secure_version = CONFIG_BOOTLOADER_APP_SECURE_VERSION,
#endif
    .version = PROJECT_VER,
    .project_name = "xc_body_stackchan",
#ifdef CONFIG_APP_COMPILE_TIME_DATE
    .time = __TIME__,
    .date = __DATE__,
#endif
    .idf_ver = IDF_VER,
    .min_efuse_blk_rev_full = CONFIG_ESP_EFUSE_BLOCK_REV_MIN_FULL,
    .max_efuse_blk_rev_full = CONFIG_ESP_EFUSE_BLOCK_REV_MAX_FULL,
    .mmu_page_size = 31 - __builtin_clz(CONFIG_MMU_PAGE_SIZE),
};

_Static_assert(
    sizeof("xc_body_stackchan") <= sizeof(esp_app_desc.project_name),
    "XC Body StackChan project name is too long");
