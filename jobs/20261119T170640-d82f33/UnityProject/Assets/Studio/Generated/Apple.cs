using UnityEngine;

public class Apple : MonoBehaviour
{
    public AppleCollector collector;

    void OnTriggerEnter(Collider other)
    {
        if (other.CompareTag("Player"))
        {
            Collect();
        }
    }

    void Collect()
    {
        if (collector != null)
        {
            collector.CollectApple();
        }
        Destroy(gameObject);
    }
}