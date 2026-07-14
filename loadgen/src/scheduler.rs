use std::time::Instant;

#[derive(Debug)]
pub struct Job {
    pub id: i64,
    pub scheduled_at: Instant,
    pub second: usize,
}

pub fn id_for(sequence: u64, seed: u64, start: i64, end: i64) -> i64 {
    let width = (end - start + 1).max(1) as u64;
    let mixed = sequence
        .wrapping_add(seed)
        .wrapping_mul(6_364_136_223_846_793_005)
        .wrapping_add(1_442_695_040_888_963_407);
    start + (mixed % width) as i64
}

#[cfg(test)]
mod tests {
    use super::id_for;

    #[test]
    fn generated_ids_are_deterministic_and_in_range() {
        let first = id_for(42, 7, 10, 20);
        assert_eq!(first, id_for(42, 7, 10, 20));
        assert!((10..=20).contains(&first));
    }
}
