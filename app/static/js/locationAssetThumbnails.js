// Порядок карточек в атласе location-asset-thumbnails.webp.
const columns = 15;
const rows = 15;
const ids = [
    'floor_opening', 'ladder', 'hatch', 'bunker_hatch', 'wall',
    'floor', 'inner_wall', 'ceiling', 'roof', 'stairs',
    'opening', 'door', 'window', 'fence', 'table',
    'chair', 'box', 'military_crate', 'barrel', 'shelf',
    'tree', 'rock', 'tent', 'campfire', 'torch',
    'wall_torch', 'fire_barrel', 'ceiling_bulb', 'wall_bulb', 'poster',
    'information_sign', 'household_bed_single', 'household_bed_double', 'household_nightstand', 'household_wardrobe',
    'household_dresser', 'household_stool', 'household_armchair', 'household_wall_shelf', 'household_storage_rack',
    'household_floor_rug', 'household_wall_rug', 'household_refrigerator', 'household_cooker', 'household_kitchen_cabinet',
    'household_sink', 'household_washbasin', 'household_toilet', 'household_bathtub', 'household_wood_stove',
    'household_generator', 'household_old_computer', 'household_desk_lamp', 'household_wood_crate', 'household_cardboard_box',
    'household_jerrycan', 'household_tv_crt', 'household_tv_flat', 'household_tv_wall', 'household_clothes_dryer',
    'household_clothes_line', 'household_gas_boiler', 'household_washing_machine', 'household_bucket_metal', 'household_bucket_plastic',
    'household_tub', 'household_plate', 'household_spoon', 'household_fork', 'household_glass',
    'household_pot', 'household_pan', 'household_cauldron', 'household_street_lamp', 'household_power_tower',
    'household_sofa', 'household_coffee_table', 'household_tv_stand', 'household_floor_lamp', 'household_kettle',
    'household_mug', 'household_bowl', 'household_knife', 'household_cutting_board', 'household_dish_rack',
    'household_dining_bench', 'household_shower', 'household_mirror', 'household_laundry_basket', 'household_basin',
    'household_towel_rail', 'household_radiator', 'household_water_heater', 'household_gas_cylinder', 'household_switchboard',
    'household_broom', 'household_mop', 'household_trash_bin', 'household_shop_counter', 'household_cash_register',
    'household_display_case', 'household_refrigerated_display', 'household_shop_scales', 'household_shopping_basket', 'household_pallet',
    'household_pallet_jack', 'household_workbench', 'household_vise', 'household_tool_cabinet', 'household_tire',
    'household_electric_pole', 'household_transformer', 'household_dumpster', 'household_street_bench', 'household_mailbox',
    'household_downpipe', 'household_well', 'household_road_sign', 'household_car_sedan', 'household_car_van',
    'household_car_burned', 'household_brick', 'household_concrete_chunk', 'household_broken_boards', 'household_rubble',
    'household_stones', 'household_fallen_tree', 'household_stump', 'household_hollow_tree', 'household_safe_small',
    'household_safe_large', 'household_sandbag', 'household_sandbag_wall', 'household_bottle', 'household_tin_can',
    'household_litter', 'household_table_setting', 'household_kitchen_set', 'household_child_bed', 'household_school_desk',
    'household_blackboard', 'household_exam_couch', 'household_medical_cabinet', 'household_sauna_bench', 'household_sauna_stove',
    'household_monument', 'household_monument_ruined', 'station_distillery', 'station_oven', 'station_gunsmith',
    'station_armorer', 'station_mixer', 'station_electrician', 'station_aeration', 'crafted_cover_m18',
    'crafted_cover_m19', 'crafted_cover_m20', 'crafted_cover_m21', 'crafted_cover_m22', 'crafted_cover_m23',
    'nature_oak_green', 'nature_oak_dry', 'nature_oak_dead', 'nature_birch_green', 'nature_birch_dry',
    'nature_birch_dead', 'nature_pine_green', 'nature_pine_dry', 'nature_pine_dead', 'nature_tall_grass_green',
    'nature_tall_grass_dry', 'nature_tall_grass_dead', 'nature_bush_green', 'nature_bush_dry', 'nature_bush_dead',
    'ruin_wall_low', 'ruin_wall_high', 'ruin_partition_low', 'ruin_partition_high', 'ruin_masonry_debris',
    'ruin_wood_debris', 'ruin_wood_opening', 'ruin_masonry_opening', 'art_rusty_barrel', 'art_wooden_crate',
    'art_chainlink_fence', 'art_rusty_metal', 'art_aged_wood', 'art_worn_plaster', 'art_dirty_earth',
    'swamp_fern_02_1', 'swamp_fern_02_2', 'swamp_fern_02_3', 'swamp_fern_02_4', 'swamp_dead_tree_trunk_02_1',
    'swamp_tree_stump_02_1', 'swamp_dry_branches_medium_01_1', 'swamp_dry_branches_medium_01_2', 'swamp_dry_branches_medium_01_3', 'swamp_pine_roots_1',
    'swamp_pine_roots_2', 'swamp_rock_moss_set_02_1', 'swamp_rock_moss_set_02_2', 'swamp_rock_moss_set_02_3', 'swamp_rock_moss_set_02_4',
    'swamp_rock_moss_set_02_5', 'swamp_rock_moss_set_02_6', 'swamp_rock_moss_set_02_7', 'swamp_wheat_wild_1', 'swamp_reeds_1',
    'swamp_dead_tree_1', 'swamp_dead_pine_1', 'swamp_water_snag', 'swamp_water_broken_tree', 'art_crow',
    'art_corrugated_iron_wall', 'art_concrete_wall', 'art_concrete_floor', 'art_cracked_concrete_wall', 'art_cracked_concrete_floor',
];
const indices = new Map(ids.map((id, index) => [id, index]));

export function locationAssetThumbnail(id) {
    const index = indices.get(id);
    if (index === undefined) return null;
    return {
        size: `${columns * 100}% ${rows * 100}%`,
        position: `${(index % columns) / (columns - 1) * 100}% ${Math.floor(index / columns) / (rows - 1) * 100}%`,
    };
}
