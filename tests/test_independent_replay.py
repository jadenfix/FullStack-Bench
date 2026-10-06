"""An independent solution cannot obtain reference code during its replay."""
from scripts.gate_task import prepare_independent_task


def test_replay_only_receives_independently_supplied_solution(tmp_path):
    task = tmp_path/'candidate'
    (task/'solution').mkdir(parents=True)
    (task/'solution'/'solve.sh').write_text('reference workflow')
    (task/'solution'/'private_patch.py').write_text('reference implementation')
    (task/'wrong_solutions').mkdir()
    (task/'wrong_solutions'/'mutant.sh').write_text('reference-derived mutant')
    (task/'tests').mkdir()
    (task/'tests'/'outcomes.py').write_text('frozen verification')
    (task/'environment'/'repo'/'solution').mkdir(parents=True)
    (task/'environment'/'repo'/'solution'/'domain.py').write_text('legitimate source directory')
    standalone = tmp_path/'standalone.sh'
    standalone.write_text('independent workflow')
    copy = prepare_independent_task(task, tmp_path/'replay', standalone)
    assert (copy/'solution'/'solve.sh').read_text() == 'independent workflow'
    assert sorted(p.name for p in (copy/'solution').iterdir()) == ['solve.sh']
    assert not (copy/'wrong_solutions').exists()
    assert (copy/'tests'/'outcomes.py').read_text() == 'frozen verification'
    assert (copy/'environment'/'repo'/'solution'/'domain.py').read_text() == 'legitimate source directory'
