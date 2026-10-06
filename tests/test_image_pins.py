"""Immutable prebuilt images cannot silently change between repeated trials."""
import pytest
from fsbench.checks import image_pin_errors


@pytest.mark.parametrize('image', ['sha256:'+'a'*64, 'registry.example/repo@sha256:'+'b'*64])
def test_immutable_solver_and_verifier_images_are_accepted(image):
    assert image_pin_errors({'environment': {'docker_image': image}, 'verifier': {'environment': {'docker_image': image}}}) == []


@pytest.mark.parametrize('image', ['repo:latest', 'repo:v1', 'sha256:abc', 'repo@sha256:'+'G'*64, 'repo@sha256:'+'a'*64+'\n'])
@pytest.mark.parametrize('role', ['environment', 'verifier.environment'])
def test_mutable_or_malformed_pin_is_rejected_in_either_environment(image, role):
    config = {'environment': {'docker_image': image}} if role == 'environment' else {'verifier': {'environment': {'docker_image': image}}}
    assert len(image_pin_errors(config)) == 1


def test_source_built_environments_remain_valid():
    assert image_pin_errors({'environment': {}, 'verifier': {'environment': {}}}) == []
