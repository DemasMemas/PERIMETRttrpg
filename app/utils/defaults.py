def empty_dict():
    return {}


def empty_list():
    return []


def default_weather_settings():
    return {
        'sun': {'enabled': True, 'intensity': 0.7},
        'fog': {'enabled': False, 'intensity': 0.5},
        'rain': {'enabled': False, 'intensity': 0.5},
        'emission': {'enabled': False, 'intensity': 0.5},
    }


def default_location_lighting():
    return {
        'mode': 'world',
        'ambient_color': '#b9c4d0',
        'ambient_intensity': 0.55,
        'directional_color': '#fff0d5',
        'directional_intensity': 1.0,
        'directional_azimuth': 45,
        'directional_elevation': 50,
        'background_color': '#283642',
        'fog_density': 0,
        'water_reflections': False,
        'natural_detail': None,
        'local_lights': [],
        'dark_tiles': [],
    }


def default_hp_zones():
    return {
        'head': {'current': 50, 'max': 50},
        'chest': {'current': 150, 'max': 150},
        'abdomen': {'current': 120, 'max': 120},
        'left_arm': {'current': 90, 'max': 90},
        'right_arm': {'current': 90, 'max': 90},
        'left_leg': {'current': 100, 'max': 100},
        'right_leg': {'current': 100, 'max': 100},
    }


def default_combat_resources():
    return {
        'action_points': 5,
        'free_actions': 1,
        'movement_points': 6,
    }
