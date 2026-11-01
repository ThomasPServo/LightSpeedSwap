#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN
/// Personal sideload build: read-only private API capability probe.
/// There are no Apple-only entitlements, setters, installs or preference writes.
@interface RadioProbe : NSObject
- (void)readWithCompletion:(void (^)(NSDictionary<NSString *, id> *result))completion NS_SWIFT_NAME(read(completion:));
@end
NS_ASSUME_NONNULL_END
