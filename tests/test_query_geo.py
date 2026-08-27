from afp.query.geo import haversine_nm


def test_same_point_is_zero_distance():
    assert haversine_nm(34.0, -118.0, 34.0, -118.0) == 0.0


def test_one_degree_latitude_is_about_sixty_nm():
    # 1 nautical mile is defined as 1 minute of arc; 1 degree of latitude
    # is therefore ~60 nm almost everywhere (small deviation from Earth's
    # slight oblateness, negligible for our purposes).
    distance = haversine_nm(0.0, 0.0, 1.0, 0.0)
    assert 59.5 < distance < 60.5


def test_symmetric():
    a = haversine_nm(34.0, -118.0, 37.0, -122.0)
    b = haversine_nm(37.0, -122.0, 34.0, -118.0)
    assert a == b
